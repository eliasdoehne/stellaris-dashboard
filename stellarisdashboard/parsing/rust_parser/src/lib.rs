// #![allow(dead_code)]
// #![allow(unused_imports)]
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::file_io::{load_save_content, SaveFile};
use crate::parser::{parse_file, parse_save};

mod parser;
mod file_io;


/// Reads the save file at the provided location and returns a dictionary of the parsed contents.
#[pyfunction]
fn parse_save_from_string(py: Python, gamestate: String) -> PyResult<PyObject> {
    match parse_file(gamestate.as_str()) {
        Ok(parsed_save) => {
            let pyobject = parsed_save.to_object(py);
            Ok(pyobject)
        }
        Err(msg) => {
            return Err(
                PyValueError::new_err(format!("Failed to parse string: {}", msg))
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
                PyValueError::new_err(format!("Failed to parse {}: {}", save_file.filename, msg))
            );
        }
    }
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_parser(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_save_from_string, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file, m)?)?;
    Ok(())
}