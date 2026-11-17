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
fn rust_slice(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_save_from_string, m)?)?;
    m.add_function(wrap_pyfunction!(parse_save_file, m)?)?;
    m.add_function(wrap_pyfunction!(budget_slice, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Evaluation prototype: extract one slice of the timeline (CountryData economy totals and
// BudgetItem rows, see CountryDataProcessor._extract_country_economy) entirely in Rust and write
// it with rusqlite, without creating any Python objects for the gamestate.
// ---------------------------------------------------------------------------------------------
use crate::parser::Value;
use rusqlite::{params_from_iter, Connection};
use std::time::Instant;
use std::collections::HashMap;


const TOTAL_RESOURCES: [&str; 12] = [
    "energy", "minerals", "alloys", "consumer_goods", "food", "trade", "unity", "influence",
    "physics_research", "society_research", "engineering_research", "biomass",
];
// column order of the budget_item slice table
const ITEM_COLUMNS: [&str; 21] = [
    "energy", "minerals", "food", "alloys", "consumer_goods", "trade", "volatile_motes",
    "exotic_gases", "rare_crystals", "living_metal", "zro", "dark_matter", "nanites",
    "minor_artifacts", "astral_threads", "biomass", "physics_research", "society_research",
    "engineering_research", "unity", "influence",
];

fn num(v: Option<&Value>) -> f64 {
    match v {
        Some(Value::Int(i)) => *i as f64,
        Some(Value::Float(f)) => *f,
        // _check_returned_number: a list starting with a number counts as that number
        Some(Value::List(l)) => match l.first() {
            Some(Value::Int(i)) => *i as f64,
            Some(Value::Float(f)) => *f,
            _ => 0.0,
        },
        _ => 0.0,
    }
}

fn get<'a, 'b>(v: &'b Value<'a>, key: &str) -> Option<&'b Value<'a>> {
    match v {
        Value::Map(m) => m.get(key),
        _ => None,
    }
}

#[pyfunction]
fn budget_slice(save_path: String, db_path: String, date: i64) -> PyResult<(f64, f64, f64, usize)> {
    let t0 = Instant::now();
    let save_file = load_save_content(save_path.as_str()).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let t_read = t0.elapsed().as_secs_f64();
    let t1 = Instant::now();
    let parsed = parse_save(&save_file).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let t_parse = t1.elapsed().as_secs_f64();

    let t2 = Instant::now();
    let err = |e: rusqlite::Error| PyValueError::new_err(e.to_string());
    let mut conn = Connection::open(db_path).map_err(err)?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;").map_err(err)?;
    let mut descriptions: HashMap<String, i64> = HashMap::new();
    {
        let mut stmt = conn.prepare("SELECT text, description_id FROM shared_description").map_err(err)?;
        let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))).map_err(err)?;
        for row in rows {
            let (text, id) = row.map_err(err)?;
            descriptions.insert(text, id);
        }
    }
    let tx = conn.transaction().map_err(err)?;
    let mut n_rows = 0usize;
    {
        let mut ins_desc = tx.prepare("INSERT INTO shared_description(text) VALUES (?1)").map_err(err)?;
        let mut ins_cd = tx.prepare(
            "INSERT INTO countrydata(date, country_id_in_game, net_energy, net_minerals, net_alloys, \
             net_consumer_goods, net_food, net_trade, net_unity, net_influence, net_physics_research, \
             net_society_research, net_engineering_research, net_biomass) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14)",
        ).map_err(err)?;
        let placeholders: Vec<String> = (1..=23).map(|i| format!("?{i}")).collect();
        let mut ins_item = tx.prepare(&format!(
            "INSERT INTO budget_item(country_data_id, budget_item_description_id, {}) VALUES ({})",
            ITEM_COLUMNS.iter().map(|c| format!("net_{c}")).collect::<Vec<_>>().join(", "),
            placeholders.join(", ")
        )).map_err(err)?;

        if let Some(Value::Map(countries)) = get(&parsed.gamestate, "country") {
            let mut ids: Vec<(i64, &Value)> = countries
                .iter()
                .filter_map(|(k, v)| k.parse::<i64>().ok().map(|i| (i, v)))
                .filter(|(_, v)| matches!(v, Value::Map(_)))
                .collect();
            ids.sort_by_key(|(i, _)| *i);
            for (country_id, country) in ids {
                let is_real = matches!(
                    get(country, "type"),
                    Some(Value::Str("default")) | Some(Value::Str("fallen_empire")) | Some(Value::Str("awakened_fallen_empire"))
                );
                let balance = get(country, "budget")
                    .and_then(|b| get(b, "current_month"))
                    .and_then(|b| get(b, "balance"));
                let mut totals = [0.0f64; 12];
                let mut items: Vec<(i64, [f64; 21])> = Vec::new();
                if let Some(Value::Map(balance)) = balance {
                    for (item_name, values) in balance.iter() {
                        let values_map = match values {
                            Value::Map(m) if !m.is_empty() => m,
                            _ => continue,
                        };
                        if *item_name == "none" {
                            continue;
                        }
                        for (i, res) in TOTAL_RESOURCES.iter().enumerate() {
                            totals[i] += num(values_map.get(res));
                        }
                        if !is_real {
                            continue;
                        }
                        let desc_id = match descriptions.get(*item_name) {
                            Some(id) => *id,
                            None => {
                                ins_desc.execute([item_name]).map_err(err)?;
                                let id = tx.last_insert_rowid();
                                descriptions.insert(item_name.to_string(), id);
                                id
                            }
                        };
                        let mut row = [0.0f64; 21];
                        for (i, col) in ITEM_COLUMNS.iter().enumerate() {
                            row[i] = match *col {
                                // save_format.get_budget_value: strategic resources carry an sr_ prefix
                                "living_metal" | "zro" | "dark_matter" => {
                                    let prefixed = format!("sr_{col}");
                                    match values_map.get(prefixed.as_str()) {
                                        Some(v) => num(Some(v)),
                                        None => num(values_map.get(col)),
                                    }
                                }
                                _ => num(values_map.get(col)),
                            };
                        }
                        items.push((desc_id, row));
                    }
                }
                let mut cd_params: Vec<rusqlite::types::Value> = vec![date.into(), country_id.into()];
                cd_params.extend(totals.iter().map(|t| rusqlite::types::Value::from(*t)));
                ins_cd.execute(params_from_iter(cd_params)).map_err(err)?;
                let country_data_id = tx.last_insert_rowid();
                for (desc_id, row) in items {
                    let mut p: Vec<rusqlite::types::Value> = vec![country_data_id.into(), desc_id.into()];
                    p.extend(row.iter().map(|t| rusqlite::types::Value::from(*t)));
                    ins_item.execute(params_from_iter(p)).map_err(err)?;
                    n_rows += 1;
                }
            }
        }
    }
    tx.commit().map_err(err)?;
    let t_extract = t2.elapsed().as_secs_f64();
    Ok((t_read, t_parse, t_extract, n_rows))
}
