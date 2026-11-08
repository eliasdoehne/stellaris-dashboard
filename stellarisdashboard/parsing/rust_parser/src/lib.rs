// #![allow(dead_code)]
// #![allow(unused_imports)]
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyInt, PyModule, PyString};

use crate::file_io::{load_save_content};
use crate::key_paths::{KeyPathFilter, WILDCARD};
use crate::parser::{filtered_value_to_pyobject, parse_file, parse_save, value_to_pyobject, Value};

mod parser;
mod file_io;
mod key_paths;


/// Parses the provided save file contents and returns a dictionary of the parsed contents.
///
/// If `key_paths` is given, only the parts of the result selected by these paths are
/// converted to Python objects, see `parse_save_file`.
#[pyfunction]
#[pyo3(signature = (gamestate, key_paths=None))]
fn parse_save_from_string(
    py: Python,
    gamestate: String,
    key_paths: Option<Vec<Bound<'_, PyAny>>>,
) -> PyResult<Py<PyAny>> {
    let filter = build_filter(key_paths)?;
    match parse_file(gamestate.as_str()) {
        Ok(parsed_save) => to_pyobject(py, &parsed_save, filter.as_ref()),
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse string: {}", msg))
            );
        }
    }
}

/// Reads the save file at the provided location and returns a dictionary of the parsed gamestate.
///
/// `key_paths` optionally restricts which parts of the gamestate are returned. Only
/// these parts are converted to Python objects, which saves time and memory when
/// the caller needs a small subset of the save. Each key path is either
/// - a string of dot-separated keys, e.g. `"galaxy.name"`, or
/// - a sequence of keys (str or int), e.g. `["country", "*", "name"]`, for keys that contain a dot.
///
/// The key `"*"` matches every key of a map. A path selects the full value at its end.
/// Paths that do not exist in the save are ignored. Lists (e.g. from repeated keys) are
/// traversed element by element, and values that are not maps or lists (such as the
/// `none` of a deleted object) are returned unchanged even if the path continues past them.
/// An empty list of paths returns an empty dictionary; `None` (the default) returns everything.
#[pyfunction]
#[pyo3(signature = (save_path, key_paths=None))]
fn parse_save_file(
    py: Python,
    save_path: String,
    key_paths: Option<Vec<Bound<'_, PyAny>>>,
) -> PyResult<Py<PyAny>> {
    let filter = build_filter(key_paths)?;
    let save_file = match load_save_content(save_path.as_str()) {
        Ok(sf) => sf,
        Err(msg) => {
            return Err(PyValueError::new_err(format!("Failed to read {}: {msg}", save_path)));
        }
    };
    match parse_save(&save_file) {
        Ok(parsed_save) => to_pyobject(py, &parsed_save.gamestate, filter.as_ref()),
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse {}: {}", save_file.filename, msg))
            );
        }
    }
}

fn to_pyobject(py: Python, value: &Value, filter: Option<&KeyPathFilter>) -> PyResult<Py<PyAny>> {
    let obj = match filter {
        Some(f) => filtered_value_to_pyobject(py, value, f)?,
        None => value_to_pyobject(py, value)?,
    };
    Ok(obj.unbind())
}

fn build_filter(key_paths: Option<Vec<Bound<'_, PyAny>>>) -> PyResult<Option<KeyPathFilter>> {
    match key_paths {
        None => Ok(None),
        Some(paths) => {
            let paths = paths.iter().map(extract_key_path).collect::<PyResult<Vec<_>>>()?;
            Ok(Some(KeyPathFilter::from_paths(paths)))
        }
    }
}

fn extract_key_path(path: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let keys: Vec<String> = if path.is_instance_of::<PyString>() {
        let path_str: String = path.extract()?;
        path_str.split('.').map(String::from).collect()
    } else {
        let mut keys = Vec::new();
        let iter = match path.try_iter() {
            Ok(iter) => iter,
            Err(_) => return Err(PyTypeError::new_err(format!(
                "A key path must be a str or a sequence of keys, got {}", path.repr()?
            ))),
        };
        for key in iter {
            keys.push(extract_key(&key?)?);
        }
        keys
    };
    if keys.iter().any(String::is_empty) {
        return Err(PyValueError::new_err(format!("Key path {} contains an empty key", path.repr()?)));
    }
    Ok(keys)
}

fn extract_key(key: &Bound<'_, PyAny>) -> PyResult<String> {
    if key.is_instance_of::<PyString>() {
        key.extract()
    } else if key.is_instance_of::<PyInt>() {
        // integer-like keys are returned as Python ints, so allow them in paths too
        Ok(key.extract::<i64>()?.to_string())
    } else {
        Err(PyTypeError::new_err(format!(
            "Keys in a key path must be str or int, got {} (use \"{}\" to match any key)",
            key.repr()?, WILDCARD
        )))
    }
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_parser(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_save_from_string, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file, m)?)?;
    Ok(())
}
