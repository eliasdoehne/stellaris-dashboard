use std::cmp::min;
use std::collections::{HashMap, HashSet, VecDeque};
use std::time::Instant;

use nom::branch::alt;
use nom::bytes::complete::{tag, take_until, take_while, take_while1};
use nom::character::complete::{char, multispace0, multispace1};
use nom::combinator::{map, map_res, opt, recognize};
use nom::error::context;
use nom::IResult;
use nom::multi::{separated_list0, separated_list1};
use nom::number::complete::double;
use nom::sequence::{delimited, preceded, separated_pair, terminated, tuple};
use pyo3::{PyAny, PyObject, PyResult, Python, ToPyObject};
use pyo3::types::PyString;
use serde::Serialize;

use crate::file_io::SaveFile;

#[derive(Serialize, Debug, PartialEq)]
#[serde(untagged)]
pub enum Value<'a> {
    Str(&'a str),
    Int(i64),
    Float(f64),
    List(Vec<Value<'a>>),
    Map(HashMap<&'a str, Value<'a>>),
}

impl ToPyObject for Value<'_> {
    fn to_object(&self, py: Python<'_>) -> PyObject {
        match self {
            Value::Str(s) => s.to_object(py),
            Value::Int(n) => n.to_object(py),
            Value::Float(x) => x.to_object(py),
            Value::List(vec) => vec.to_object(py),
            Value::Map(hm) => hm.to_object(py),
        }
    }
}

impl Value<'_> {
    pub fn lookup<'a>(&'a self, key_seq: Vec<&'a str>) -> Option<&'a Value> {
        Value::lookup_inner(&self, VecDeque::from(key_seq)).ok()
    }

    fn lookup_inner<'a, 'b>(node: &'a Value, mut key_seq: VecDeque<&'a str>) -> Result<&'a Value<'a>, &'a str> {
        for key in &key_seq {
            let next_node = match node {
                Value::Map(hm) => {
                    match hm.get(key) {
                        Some(next_node) => next_node,
                        _ => {
                            return Err("Could not find matching node");
                        }
                    }
                }
                Value::List(list) => {
                    if let Ok(int_key) = key.parse::<usize>() {
                        match list.get(int_key) {
                            Some(v) => v,
                            None => return Err("Index failed")
                        }
                    } else {
                        return Err("Could not convert key to list index");
                    }
                }
                v => {
                    println!("Found unexpected scalar value: {:?}", v);
                    return Err("Could not find matching node");
                }
            };
            key_seq.remove(0);
            return Value::lookup_inner(next_node, key_seq);
        }
        Ok(&node)
    }
}

pub struct ParsedSaveFile<'a> {
    pub gamestate: Value<'a>,
    pub meta: Value<'a>,
    pub game_id: String,
    pub parsed_time: Instant,
}

pub fn parse_save<'a>(save_file: &'a SaveFile) -> Result<ParsedSaveFile<'a>, &'static str> {
    let start = Instant::now();
    let meta_contents = save_file.meta.as_str();
    let meta = match parse_file(meta_contents) {
        Ok(result) => {
            result
        }
        Err(_) => return Err("Failed to parse save metadata")
    };

    let gamestate = match parse_file((&save_file.gamestate).as_str()) {
        Ok(result) => {
            result
        }
        Err(_) => return Err("Failed to parse save gamestate")
    };

    let duration = start.elapsed();
    println!("Parsed save contents in {:?}", duration);
    Ok(ParsedSaveFile {
        gamestate,
        meta,
        game_id: save_file.game_id.clone(),
        parsed_time: Instant::now(),
    })
}


pub fn parse_file<'a>(input: &'a str) -> Result<Value<'a>, &str> {
    match parse_map_inner(input) {
        Ok((remainder, hm)) => {
            if remainder.chars().all(char::is_whitespace) {
                Ok(Value::Map(hm))
            } else {
                Err(remainder)
            }
        }
        Err(_) => Err("Parsing failed")
    }
}

fn parse_value(input: &str) -> IResult<&str, Value> {
    // debug_str(input);
    alt(
        (
            context("date", map(parse_date_str, Value::Str)),
            context("int", map(parse_int, Value::Int)),
            context("float", map(parse_float, Value::Float)),
            context("str", map(parse_str, Value::Str)),
            context("unquoted_str", map(parse_unquoted_str, Value::Str)),
            context("list", map(parse_list, Value::List)),
            context("map", map(parse_map, Value::Map)),
        )
    )(input)
}

fn parse_list(input: &str) -> IResult<&str, Vec<Value>> {
    delimited(
        terminated(tag("{"), multispace0),
        parse_list_inner,
        preceded(multispace0, tag("}")),
    )(input)
}

fn parse_list_inner(input: &str) -> IResult<&str, Vec<Value>> {
    separated_list0(
        multispace1,
        parse_value,
    )(input)
}

fn parse_map(input: &str) -> IResult<&str, HashMap<&str, Value>> {
    delimited(
        preceded(multispace0, tag("{")),
        parse_map_inner,
        preceded(multispace0, tag("}")),
    )(input)
}

fn parse_map_inner(input: &str) -> IResult<&str, HashMap<&str, Value>> {
    let mut handled_nested_list_keys: HashSet<&str> = HashSet::new();
    match delimited(
        multispace0,
        separated_list1(
            multispace1,
            separated_pair(
                alt((parse_str, parse_unquoted_str)),
                delimited(multispace0, opt(tag("=")), multispace0),
                parse_value,
            ),
        ), multispace0,
    )(input) {
        Ok((remainder, kv_list)) => {
            let mut hm = HashMap::new();
            for (key, value) in kv_list {
                let old_value = hm.remove(key);
                match old_value {
                    None => {
                        hm.insert(key, value);
                    }
                    Some(Value::List(mut vec)) => {
                        let value_to_insert = match (value, handled_nested_list_keys.contains(key)) {
                            (Value::List(new_vec), false) => {
                                handled_nested_list_keys.insert(key);
                                Value::List(Vec::from([Value::List(vec), Value::List(new_vec)]))
                            }
                            (value, _) => {
                                vec.push(value);
                                Value::List(vec)
                            }
                        }
                            ;
                        hm.insert(key, value_to_insert);
                    }
                    Some(other_value) => {
                        hm.insert(key, Value::List(vec![other_value, value]));
                    }
                }
            }
            Ok((remainder, hm))
        }
        Err(e) => Err(e),
    }
}

fn parse_date_str(input: &str) -> IResult<&str, &str> {
    recognize(tuple((
        take_while(|c: char| c.is_digit(10)),
        char('.'),
        take_while(|c: char| c.is_digit(10)),
        char('.'),
        take_while(|c: char| c.is_digit(10))
    )))(input)
}

fn parse_int(input: &str) -> IResult<&str, i64> {
    preceded(
        multispace0,
        map_res(recognize(double), |s: &str| s.parse::<i64>()),
    )(input)
}

fn parse_float(input: &str) -> IResult<&str, f64> {
    preceded(
        multispace0,
        map_res(recognize(double), |s: &str| s.parse::<f64>()),
    )(input)
}

fn parse_str(input: &str) -> IResult<&str, &str> {
    preceded(
        multispace0,
        delimited(char('"'), take_until("\""), char('"')),
    )(input)
}

fn _is_valid_unquoted_str_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == ':' || c == '.'
}

fn parse_unquoted_str(input: &str) -> IResult<&str, &str> {
    preceded(
        multispace0,
        take_while1(_is_valid_unquoted_str_char),
    )(input)
}

#[allow(dead_code)]
pub fn debug_str(input: &str) -> () {
    let prefix = &input[..min(input.len(), 50)];
    println!("{:?}", prefix);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_int() {
        assert_eq!(parse_value("123"), Ok(("", Value::Int(123))));
        assert_eq!(parse_value("0"), Ok(("", Value::Int(0))));
        assert_eq!(parse_value(" 007"), Ok(("", Value::Int(7))));
        assert_eq!(parse_value("-1"), Ok(("", Value::Int(-1))));
    }

    #[test]
    fn test_parse_float() {
        assert_eq!(parse_value("1.0"), Ok(("", Value::Float(1.0))));
        assert_eq!(parse_value("-1.0"), Ok(("", Value::Float(-1.0))));
    }

    #[test]
    fn test_parse_date() {
        assert_eq!(parse_value("2200.04.03"), Ok(("", Value::Str("2200.04.03"))));
        assert_eq!(parse_value("2243.01.03"), Ok(("", Value::Str("2243.01.03"))));
        assert_eq!(parse_value("1.1.1"), Ok(("", Value::Str("1.1.1"))));
    }

    #[test]
    fn test_parse_str() {
        assert_eq!(parse_value("\"word\""), Ok(("", Value::Str("word"))));
        assert_eq!(
            parse_value("\"This is a full sentence!.?\""),
            Ok(("", Value::Str("This is a full sentence!.?")))
        );
        assert_eq!(
            parse_value("\"Unicode ßäöü😂\""),
            Ok(("", Value::Str("Unicode ßäöü😂")))
        );
        assert_eq!(
            parse_value("\"flag_human_9.dds\""),
            Ok(("", Value::Str("flag_human_9.dds")))
        );
    }

    #[test]
    fn test_parse_unquoted_str() {
        assert_eq!(
            parse_value("traits"),
            Ok(("", Value::Str("traits")))
        );
        assert_eq!(
            parse_value("target:debris_field_01"),
            Ok(("", Value::Str("target:debris_field_01")))
        );
    }

    #[test]
    fn test_parse_list() {
        assert_eq!(
            parse_value("{}"),
            Ok(("", Value::List(Vec::new())))
        );
        assert_eq!(
            parse_value("{1}"),
            Ok(("", Value::List(Vec::from([Value::Int(1)]))))
        );
        assert_eq!(
            parse_value("{1 2\t3\n4}"),
            Ok(("", Value::List(Vec::from([Value::Int(1), Value::Int(2), Value::Int(3), Value::Int(4)]))))
        );
        assert_eq!(
            parse_value("{1 \"text\" 3}"),
            Ok(("", Value::List(Vec::from([Value::Int(1), Value::Str("text"), Value::Int(3)]))))
        );
        assert_eq!(
            parse_value("{1 {\"inner\"} 3}"),
            Ok(
                (
                    "",
                    Value::List(Vec::from([
                        Value::Int(1),
                        Value::List(Vec::from([Value::Str("inner")])),
                        Value::Int(3)
                    ]))
                )
            )
        );
    }

    #[test]
    fn test_parse_map() {
        assert_eq!(
            parse_value("{a.1=2}"),
            Ok(
                (
                    "",
                    Value::Map(HashMap::from([
                        (("a.1"), Value::Int(2)),
                    ]))
                )
            )
        );

        assert_eq!(
            parse_value("{2=2243.01.03 9=2243.01.10 12=2243.01.13}"),
            Ok(
                (
                    "",
                    Value::Map(HashMap::from([
                        (("2"), Value::Str("2243.01.03")),
                        (("9"), Value::Str("2243.01.10")),
                        (("12"), Value::Str("2243.01.13")),
                    ]))
                )
            )
        );

        assert_eq!(
            parse_value("{x=1\ny=73.0}"),
            Ok(
                (
                    "",
                    Value::Map(HashMap::from([
                        (("x"), Value::Int(1)),
                        (("y"), Value::Float(73.0)),
                    ]))
                )
            )
        );
        assert_eq!(
            parse_value("{x=1 y={x=1 y=73.0 z=\"asdf\"\na={\"Anniversary Portraits\"\n \t\"Apocalypse\"}}}"),
            Ok(
                (
                    "",
                    Value::Map(HashMap::from([
                        (("x"), Value::Int(1)),
                        (
                            ("y"),
                            Value::Map(HashMap::from([
                                (("x"), Value::Int(1)),
                                (("y"), Value::Float(73.0)),
                                (("z"), Value::Str("asdf")),
                                (("a"), Value::List(Vec::from([
                                    Value::Str("Anniversary Portraits"), Value::Str("Apocalypse")
                                ]))),
                            ]))
                        ),
                    ]))
                )
            )
        );

        assert_eq!(
            parse_value("{intel_manager={ intel={ { 13 { intel=0 stale_intel={} } } { 62 {intel=0 stale_intel={}}} { 63 {intel=0 stale_intel={}}} }}}"),
            Ok((
                "",
                Value::Map(HashMap::from([
                    (
                        "intel_manager",
                        Value::Map(HashMap::from([
                            (
                                "intel",
                                Value::List(
                                    vec![
                                        Value::List(vec![
                                            Value::Int(13), Value::Map(HashMap::from([("intel", Value::Int(0)), ("stale_intel", Value::List(vec![]))])),
                                        ]),
                                        Value::List(vec![
                                            Value::Int(62), Value::Map(HashMap::from([("intel", Value::Int(0)), ("stale_intel", Value::List(vec![]))])),
                                        ]),
                                        Value::List(vec![
                                            Value::Int(63), Value::Map(HashMap::from([("intel", Value::Int(0)), ("stale_intel", Value::List(vec![]))])),
                                        ]),
                                    ]
                                )
                            )
                        ]))
                    )
                ]))
            )
            ));
        assert_eq!(
            parse_value("{intel_manager={ intel={ { 67 { intel=10 stale_intel={ } } } } }}"),
            Ok((
                "",
                Value::Map(HashMap::from([
                    (
                        "intel_manager",
                        Value::Map(HashMap::from([
                            (
                                "intel",
                                Value::List(
                                    vec![
                                        Value::List(vec![
                                            Value::Int(67), Value::Map(HashMap::from([("intel", Value::Int(10)), ("stale_intel", Value::List(vec![]))])),
                                        ]),
                                    ]
                                )
                            )
                        ]))
                    )
                ]))
            )
            ));
    }

    #[test]
    fn test_json_representation() {
        let v = parse_value("{intel_manager={intel={{13 {intel=0 stale_intel={}}} {62 {intel=0 stale_intel={}}} {63 {intel=0 stale_intel={}}} }}}").expect("").1;
        assert_eq!(
            serde_json::json!(v).to_string(),
            "{\"intel_manager\":{\"intel\":[[13,{\"intel\":0,\"stale_intel\":[]}],[62,{\"intel\":0,\"stale_intel\":[]}],[63,{\"intel\":0,\"stale_intel\":[]}]]}}"
        );

        let v = parse_value("{intel_manager={ intel={ { 67 { intel=10 stale_intel={ } } } } }}").expect("").1;
        assert_eq!(
            serde_json::json!(v).to_string(),
            "{\"intel_manager\":{\"intel\":[[67,{\"intel\":10,\"stale_intel\":[]}]]}}"
        );
    }

    #[test]
    fn test_parse_map_repeated_key() {
        assert_eq!(
            parse_value("{x=1 x=1 y=1 y=2 z=1 z=\"asdf\"}"),
            Ok(
                (
                    "",
                    Value::Map(HashMap::from([
                        (
                            "x", Value::List(Vec::from([Value::Int(1), Value::Int(1)]))
                        ),
                        (
                            "y", Value::List(Vec::from([Value::Int(1), Value::Int(2)]))
                        ),
                        (
                            "z", Value::List(Vec::from([Value::Int(1), Value::Str("asdf")]))),
                    ]))
                )
            )
        );
        assert_eq!(
            parse_value("{x={1 1 1} x={2 2 2} x={3 3 3}}").unwrap().1,
            Value::Map(HashMap::from([
                (
                    "x",
                    Value::List(Vec::from([
                        Value::List(Vec::from([Value::Int(1), Value::Int(1), Value::Int(1)])),
                        // when the second key with x is encountered, values above should be place in a NESTED list
                        Value::List(Vec::from([Value::Int(2), Value::Int(2), Value::Int(2)])),
                        Value::List(Vec::from([Value::Int(3), Value::Int(3), Value::Int(3)])),
                    ]))
                ),
            ]))
        );
    }

    #[test]
    fn test_parse_file() {
        assert_eq!(
            parse_file(r#"
            required_dlcs={
                "Ancient Relics Story Pack"
                "Anniversary Portraits"
                "Apocalypse"
            }"#
            ).unwrap(),
            Value::Map(
                HashMap::from([
                    (
                        ("required_dlcs"),
                        Value::List(
                            Vec::from([
                                Value::Str("Ancient Relics Story Pack"),
                                Value::Str("Anniversary Portraits"),
                                Value::Str("Apocalypse"),
                            ]))
                    ),
                ])
            )
        );
        assert_eq!(
            parse_file(r#"
            ship_names={
                "HUMAN1_SHIP_Drake"=1
                "HUMAN1_SHIP_Shenandoah"=1
                "HUMAN1_SHIP_Chaoyang"=1
            }"#
            ).unwrap(),
            Value::Map(
                HashMap::from([
                    (
                        ("ship_names"),
                        Value::Map(HashMap::from([
                            (
                                ("HUMAN1_SHIP_Drake"), Value::Int(1)
                            ),
                            (
                                ("HUMAN1_SHIP_Shenandoah"), Value::Int(1)
                            ),
                            (
                                ("HUMAN1_SHIP_Chaoyang"), Value::Int(1)
                            ),
                        ]))
                    ),
                ])
            )
        );
        assert_eq!(
            parse_file(r#"
            flag={
                icon={
                    category="human"
                    file="flag_human_9.dds"
                }
                background={
                    category="backgrounds"
                    file="00_solid.dds"
                }
                colors={
                    "blue"
                    "black"
                    "null"
                    "null"
                }
            }"#
            ).unwrap(),
            Value::Map(HashMap::from([
                (
                    ("flag"),
                    Value::Map(HashMap::from([
                        (
                            ("icon"),
                            Value::Map(HashMap::from([
                                (
                                    ("category"), Value::Str("human")
                                ),
                                (
                                    ("file"), Value::Str("flag_human_9.dds")
                                ),
                            ]))
                        ),
                        (
                            ("background"),
                            Value::Map(HashMap::from([
                                (
                                    ("category"), Value::Str("backgrounds")
                                ),
                                (
                                    ("file"), Value::Str("00_solid.dds")
                                ),
                            ]))
                        ),
                        (
                            ("colors"),
                            Value::List(Vec::from([
                                Value::Str("blue"),
                                Value::Str("black"),
                                Value::Str("null"),
                                Value::Str("null"),
                            ]))
                        ),
                    ])),
                ),
            ]))
        );


        assert_eq!(
            parse_file(
                r#"intel={ { 77 { intel=10 stale_intel={ } } } }"#
            ).unwrap(),
            Value::Map(HashMap::from([
                (
                    "intel",
                    Value::List(Vec::from([
                        Value::List(Vec::from([
                            Value::Int(77),
                            Value::Map(HashMap::from([
                                ("intel", Value::Int(10)),
                                ("stale_intel", Value::List(Vec::new())),
                            ])),
                        ])),
                    ]))
                ),
            ])
            )
        );
    }
}