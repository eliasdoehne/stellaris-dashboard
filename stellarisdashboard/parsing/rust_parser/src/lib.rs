// #![allow(dead_code)]
// #![allow(unused_imports)]
use std::cell::RefCell;
use std::time::Instant;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyModule};

use crate::fast::{Filter, NullSink, PySink, TapeSink};
use crate::file_io::{load_save_content, read_member_bytes};
use crate::parser::{parse_file, parse_save, value_to_pyobject};

mod fast;
mod file_io;
mod parser;

thread_local! {
    /// Phase timings (seconds) of the last `*_fast` / `*_timed` call on this thread.
    static TIMINGS: RefCell<Vec<(&'static str, f64)>> = const { RefCell::new(Vec::new()) };
}

struct Phases {
    last: Instant,
    phases: Vec<(&'static str, f64)>,
}

impl Phases {
    fn new() -> Self {
        Phases { last: Instant::now(), phases: Vec::new() }
    }
    fn mark(&mut self, name: &'static str) {
        let now = Instant::now();
        self.phases.push((name, (now - self.last).as_secs_f64()));
        self.last = now;
    }
    fn store(self) {
        TIMINGS.with(|t| *t.borrow_mut() = self.phases);
    }
}

/// Phase timings (wall-clock seconds) of the last `parse_*_fast` / `parse_save_file_timed` call.
#[pyfunction]
fn last_timings(py: Python) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    TIMINGS.with(|t| -> PyResult<()> {
        for (k, v) in t.borrow().iter() {
            d.set_item(*k, *v)?;
        }
        Ok(())
    })?;
    Ok(d.into_any().unbind())
}

/// Reads the save file at the provided location and returns a dictionary of the parsed contents.
#[pyfunction]
fn parse_save_from_string(py: Python, gamestate: String) -> PyResult<Py<PyAny>> {
    match parse_file(gamestate.as_str()) {
        Ok(parsed_save) => Ok(value_to_pyobject(py, &parsed_save)?.unbind()),
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse string: {}", msg))
            );
        }
    }
}

#[pyfunction]
fn parse_save_file(py: Python, save_path: String) -> PyResult<Py<PyAny>> {
    let save_file = match load_save_content(save_path.as_str()) {
        Ok(sf) => sf,
        Err(msg) => {
            return Err(PyValueError::new_err(format!("Failed to read {}: {msg}", save_path)));
        }
    };
    match parse_save(&save_file) {
        Ok(parsed_save) => Ok(value_to_pyobject(py, &parsed_save.gamestate)?.unbind()),
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse {}: {}", save_file.filename, msg))
            );
        }
    }
}

/// The legacy pipeline of `parse_save_file`, with each phase timed (see `last_timings`).
#[pyfunction]
fn parse_save_file_timed(py: Python, save_path: String) -> PyResult<Py<PyAny>> {
    let mut ph = Phases::new();
    let err = |m: String| PyValueError::new_err(m);
    let meta = read_member_bytes(&save_path, "meta", false).map_err(err)?;
    let gamestate = read_member_bytes(&save_path, "gamestate", false).map_err(err)?;
    ph.mark("inflate");
    let meta = String::from_utf8(meta).map_err(|_| err("invalid UTF-8".into()))?;
    let gamestate = String::from_utf8(gamestate).map_err(|_| err("invalid UTF-8".into()))?;
    ph.mark("utf8");
    let _meta = parse_file(&meta).map_err(|_| err("Failed to parse save metadata".into()))?;
    let value = parse_file(&gamestate).map_err(|_| err("Failed to parse save gamestate".into()))?;
    ph.mark("parse");
    let obj = value_to_pyobject(py, &value)?.unbind();
    ph.mark("to_python");
    drop(value);
    ph.mark("drop_tree");
    drop(gamestate);
    ph.mark("drop_text");
    ph.store();
    Ok(obj)
}

fn parse_err(what: &str, e: fast::ParseError) -> PyErr {
    PyValueError::new_err(format!("Failed to parse {}: {} at byte {}", what, e.msg, e.pos))
}

/// Parse validated UTF-8 `input` into Python objects.
///
/// mode: "direct" builds the Python objects while parsing (GIL held);
///       "tape" parses into an event tape with the GIL released and then builds the objects;
///       "value" builds the legacy Rust tree and converts it with the legacy conversion;
///       "null" only runs the lexer / parser and returns None.
fn parse_bytes(
    py: Python,
    input: &[u8],
    filter: &Filter,
    mode: &str,
    cache: bool,
    gc_untrack: bool,
    what: &str,
    ph: &mut Phases,
) -> PyResult<Py<PyAny>> {
    match mode {
        "direct" => {
            let mut sink = PySink::new(py, cache, gc_untrack);
            fast::parse_document(input, filter, &mut sink).map_err(|e| parse_err(what, e))?;
            ph.mark("parse_and_build");
            let obj = sink.take_result().expect("result");
            drop(sink);
            ph.mark("drop_cache");
            Ok(obj)
        }
        "tape" => {
            let tape = py
                .detach(|| {
                    let mut tape = TapeSink::new(input);
                    fast::parse_document(input, filter, &mut tape).map(|_| tape)
                })
                .map_err(|e| parse_err(what, e))?;
            ph.mark("parse_tape");
            let mut sink = PySink::new(py, cache, gc_untrack);
            fast::replay(input, &tape.events, &mut sink).map_err(|e| parse_err(what, e))?;
            ph.mark("build_from_tape");
            let obj = sink.take_result().expect("result");
            drop(sink);
            drop(tape);
            ph.mark("drop_cache");
            Ok(obj)
        }
        "value" => {
            // SAFETY: callers pass validated UTF-8
            let text = unsafe { std::str::from_utf8_unchecked(input) };
            let value = fast::parse_to_value(text, filter).map_err(|e| parse_err(what, e))?;
            ph.mark("parse");
            let obj = value_to_pyobject(py, &value)?.unbind();
            ph.mark("to_python");
            drop(value);
            ph.mark("drop_tree");
            Ok(obj)
        }
        "null" => {
            fast::parse_document(input, filter, &mut NullSink).map_err(|e| parse_err(what, e))?;
            ph.mark("parse");
            Ok(py.None())
        }
        _ => Err(PyValueError::new_err("mode must be one of: direct, tape, value, null")),
    }
}

/// Prototype replacement for `parse_save_from_string`.
///
/// sections: None for everything, an iterable of top-level keys to keep, or a nested dict
/// (see `Filter::from_py`).
#[pyfunction]
#[pyo3(signature = (gamestate, sections=None, mode="direct", cache=true))]
fn parse_save_from_string_fast(
    py: Python,
    gamestate: &str,
    sections: Option<&Bound<'_, PyAny>>,
    mode: &str,
    cache: bool,
) -> PyResult<Py<PyAny>> {
    let filter = match sections {
        Some(s) => Filter::from_py(s)?,
        None => Filter::All,
    };
    let mut ph = Phases::new();
    let r = parse_bytes(py, gamestate.as_bytes(), &filter, mode, cache, false, "string", &mut ph);
    ph.store();
    r
}

/// Prototype replacement for `parse_save_file` (same result for `sections=None`).
///
/// gc_untrack (experimental): untrack the created lists and dicts from the cyclic garbage
/// collector, which otherwise traverses all of them once after the call. The result is a tree
/// without cycles, but a reference cycle created later through one of these containers would
/// never be collected.
#[pyfunction]
#[pyo3(signature = (save_path, sections=None, mode="direct", cache=true, fast_inflate=true, gc_untrack=false))]
fn parse_save_file_fast(
    py: Python,
    save_path: &str,
    sections: Option<&Bound<'_, PyAny>>,
    mode: &str,
    cache: bool,
    fast_inflate: bool,
    gc_untrack: bool,
) -> PyResult<Py<PyAny>> {
    let filter = match sections {
        Some(s) => Filter::from_py(s)?,
        None => Filter::All,
    };
    let mut ph = Phases::new();
    // Reading, inflating and validating need no Python objects: release the GIL.
    let loaded = py.detach(|| -> Result<(Vec<u8>, f64, f64), String> {
        let t0 = Instant::now();
        let meta = read_member_bytes(save_path, "meta", fast_inflate)?;
        let gamestate = read_member_bytes(save_path, "gamestate", fast_inflate)?;
        let t1 = Instant::now();
        // Like the legacy parser, require a well-formed `meta` file.
        std::str::from_utf8(&meta).map_err(|_| "meta is not valid UTF-8".to_string())?;
        fast::parse_document(&meta, &Filter::All, &mut NullSink)
            .map_err(|_| "Failed to parse save metadata".to_string())?;
        std::str::from_utf8(&gamestate).map_err(|_| "gamestate is not valid UTF-8".to_string())?;
        let t2 = Instant::now();
        Ok((gamestate, (t1 - t0).as_secs_f64(), (t2 - t1).as_secs_f64()))
    });
    let (gamestate, t_inflate, t_utf8) = loaded
        .map_err(|msg| PyValueError::new_err(format!("Failed to read {}: {}", save_path, msg)))?;
    ph.phases.push(("inflate", t_inflate));
    ph.phases.push(("utf8", t_utf8));
    ph.last = Instant::now();
    let r = parse_bytes(py, &gamestate, &filter, mode, cache, gc_untrack, save_path, &mut ph);
    drop(gamestate);
    ph.mark("drop_text");
    ph.store();
    r
}

/// Size statistics of a save: `{path: (count, bytes, values)}` for every key path up to
/// `max_depth` components (integer keys are written as `*`, list levels as `[]`), plus
/// `""` for the whole gamestate. For deciding what a projection would save.
#[pyfunction]
#[pyo3(signature = (save_path, max_depth=1, sections=None))]
fn save_file_stats(
    py: Python,
    save_path: &str,
    max_depth: usize,
    sections: Option<&Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let filter = match sections {
        Some(s) => Filter::from_py(s)?,
        None => Filter::All,
    };
    let err = |m: String| PyValueError::new_err(m);
    let gamestate = read_member_bytes(save_path, "gamestate", true).map_err(err)?;
    std::str::from_utf8(&gamestate).map_err(|_| err("gamestate is not valid UTF-8".into()))?;
    let mut sink = fast::StatsSink::new(&gamestate, max_depth);
    fast::parse_document(&gamestate, &filter, &mut sink).map_err(|e| parse_err(save_path, e))?;
    let d = PyDict::new(py);
    for (path, s) in sink.stats.iter() {
        d.set_item(path, (s.count, s.bytes, s.values))?;
    }
    d.set_item("", (1, gamestate.len(), sink.total_values()))?;
    Ok(d.into_any().unbind())
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_parser(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_save_from_string, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file_timed, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_from_string_fast, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file_fast, m)?)?;
    m.add_function(wrap_pyfunction!(last_timings, m)?)?;
    m.add_function(wrap_pyfunction!(save_file_stats, m)?)?;
    Ok(())
}
