use std::time::Instant;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyMapping};

use crate::file_io::load_save_content;
use crate::parser::parse_save;

mod parser;
mod file_io;

/// Reads the save file at the provided location and returns a dictionary of the parsed contents.
#[pyfunction]
fn parse_save_file_to_json(save_path: String) -> PyResult<String> {
    println!("Reading save {}", save_path);
    let save_file = match load_save_content(save_path.as_str()) {
        Ok(sf) => sf,
        Err(msg) => {
            return Err(PyValueError::new_err(format!("Failed to read {}: {msg}", save_path)));
        }
    };
    match parse_save(&save_file) {
        Ok(parsed_save) => {
            Ok(serde_json::json!(parsed_save.gamestate).to_string())
        }
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse {}: {}", save_path, msg))
            );
        }
    }
}

#[pyfunction]
fn parse_save_file(py: Python, save_path: String) -> PyResult<PyObject> {
    println!("Reading save {}", save_path);
    let save_file = match load_save_content(save_path.as_str()) {
        Ok(sf) => sf,
        Err(msg) => {
            return Err(PyValueError::new_err(format!("Failed to read {}: {msg}", save_path)));
        }
    };
    match parse_save(&save_file) {
        Ok(parsed_save) => {
            let pyobject = parsed_save.gamestate.to_object(py);
            Ok(pyobject)
        }
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse {}: {}", save_path, msg))
            );
        }
    }
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_parser(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_save_file_to_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file, m)?)?;
    Ok(())
}