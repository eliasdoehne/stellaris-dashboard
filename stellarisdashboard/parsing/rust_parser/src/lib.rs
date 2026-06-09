// #![allow(dead_code)]
// #![allow(unused_imports)]
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyModule};

use crate::file_io::{load_save_content};
use crate::parser::{parse_file, parse_save, value_to_pyobject};

mod parser;
mod file_io;


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

/// A Python module implemented in Rust.
#[pymodule]
fn rust_parser(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_save_from_string, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file, m)?)?;
    Ok(())
}