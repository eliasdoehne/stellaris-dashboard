use std::cmp::min;
use std::collections::{HashMap, HashSet};
use std::fmt::{Display, Formatter};
use std::time::Instant;

use nom::branch::alt;
use nom::bytes::complete::{tag, take_while, take_while1};
use nom::bytes::streaming::escaped;
use nom::character::complete::{char, multispace0, multispace1, none_of, one_of};
use nom::combinator::{map, map_res, not, recognize};
use nom::error::context;
use nom::IResult;
use nom::multi::{separated_list0, separated_list1};
use nom::number::complete::double;
use nom::sequence::{delimited, pair, preceded, separated_pair, terminated, tuple};
use pyo3::{PyObject, Python, ToPyObject};
use pyo3::types::IntoPyDict;
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
    Color((&'a str, f64, f64, f64)),
}

impl ToPyObject for Value<'_> {
    fn to_object(&self, py: Python<'_>) -> PyObject {
        match self {
            Value::Str(s) => s.to_object(py),
            Value::Int(n) => n.to_object(py),
            Value::Float(x) => x.to_object(py),
            Value::List(vec) => vec.to_object(py),
            Value::Map(hm) => {
                // For consistency with the old parser behaviour, attempt to convert each hashmap key
                // into an integer when building the python dict.
                let mut key_vals = Vec::new();
                for (key, val) in hm.into_iter() {
                    let fixed_key = match key.parse::<i64>() {
                        Ok(i) => i.to_object(py),
                        Err(_) => key.to_object(py),
                    };
                    key_vals.push((fixed_key, val));
                }
                key_vals.into_py_dict(py).to_object(py)
            }
            Value::Color(color_tuple) => color_tuple.to_object(py),
        }
    }
}

impl Display for Value<'_> {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Value::Str(s) => write!(f, "{}", s),
            Value::Int(n) => write!(f, "{}", n),
            Value::Float(x) => write!(f, "{}", x),
            Value::List(vec) => write!(f, "[{:?}]", vec),
            Value::Map(hm) => write!(f, "{:?}", hm),
            Value::Color((space, v1, v2, v3)) => write!(f, "{} {{ {} {} {} }}", space, v1, v2, v3),
        }
    }
}


pub struct ParsedSaveFile<'a> {
    pub gamestate: Value<'a>,
    pub meta: Value<'a>,
    pub game_id: String,
    pub parsed_time: Instant,
}

pub fn parse_save<'a>(save_file: &'a SaveFile) -> Result<ParsedSaveFile<'a>, &'static str> {
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
        _ => Err("Parsing failed")
    }
}

fn parse_value(input: &str) -> IResult<&str, Value> {
    // print!("Parsing next value from: ");
    // debug_str(input);
    alt(
        (
            context("date", map(parse_date_str, Value::Str)),
            context("int", map(parse_int, Value::Int)),
            context("float", map(parse_float, Value::Float)),
            context("str", map(parse_str, Value::Str)),
            context("color", map(parse_color, Value::Color)),
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
    match parse_map_kv_list(input) {
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

fn parse_map_kv_list(input: &str) -> IResult<&str, Vec<(&str, Value<>)>> {
    delimited(
        multispace0,
        separated_list1(
            multispace1,
            parse_map_key_value_pair,
        ),
        multispace0,
    )(input)
}

fn parse_map_key_value_pair<'a>(input: &'a str) -> IResult<&str, (&str, Value<'a>)> {
    separated_pair(
        alt((parse_str, parse_unquoted_str)),
        parse_map_key_value_separator,
        alt((
            // key can be missing in some cases, see test_skipped_key_in_mapping.
            // in this case, discard the second key
            preceded(pair(parse_unquoted_str, parse_map_key_value_separator), parse_value),
            parse_value,
        )),
    )(input)
}

fn parse_map_key_value_separator(input: &str) -> IResult<&str, &str> {
    delimited(multispace0, tag("="), multispace0)(input)
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
        preceded(
            not(tag("nan")),
            map_res(recognize(double), |s: &str| s.parse::<f64>()),
        ),
    )(input)
}

fn parse_str(input: &str) -> IResult<&str, &str> {
    preceded(
        multispace0,
        delimited(tag("\""), escaped(none_of("\"\\"), '\\', one_of("\"\\")), tag("\"")),
    )(input)
}

fn _is_valid_unquoted_str_char(c: char) -> bool {
    !c.is_whitespace() &&
    c != '"' &&
    c != '=' &&
    c != '{' &&
    c != '}' &&
    c != '<' &&
    c != '>' &&
    c != '[' &&
    c != ']' &&
    c != '#' &&
    c != '$' &&
    c != '|'
}

fn parse_unquoted_str(input: &str) -> IResult<&str, &str> {
    preceded(
        multispace0,
        take_while1(_is_valid_unquoted_str_char),
    )(input)
}

fn parse_color(input: &str) -> IResult<&str, (&str, f64, f64, f64)> {
    let (input, color_space) = preceded(multispace0, alt((tag("rgb"), tag("hsv"))))(input)?;
    let (input, _) = preceded(multispace0, tag("{"))(input)?;
    let (input, (v1, v2, v3)) = tuple((parse_float, parse_float, parse_float))(input)?;
    let (input, _) = preceded(multispace0, tag("}"))(input)?;
    Ok((input, (color_space, v1, v2, v3)))
}

#[allow(dead_code)]
pub fn debug_str(input: &str) -> () {
    let prefix: String = input.chars().take(150).collect();
    println!("{:?}", prefix);
    println!("{}", prefix);
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
        assert_eq!(
            parse_value(r#""\"Escaped\"""#),
            Ok(("", Value::Str(r#"\"Escaped\""#)))
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
            parse_file(r#"key1=value1
                key2={ list of values }
                key3={ {} {1 2 3} }"#),
            Ok(
                Value::Map(HashMap::from([
                    ("key1", Value::Str("value1")),
                    ("key2", Value::List(vec![Value::Str("list"), Value::Str("of"), Value::Str("values")])),
                    ("key3", Value::List(vec![
                        Value::List(vec![]),
                        Value::List(vec![Value::Int(1), Value::Int(2), Value::Int(3)]),
                    ])),
                ])
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
            r#"{"intel_manager":{"intel":[[13,{"intel":0,"stale_intel":[]}],[62,{"intel":0,"stale_intel":[]}],[63,{"intel":0,"stale_intel":[]}]]}}"#
        );

        let v = parse_value("{intel_manager={ intel={ { 67 { intel=10 stale_intel={ } } } } }}").expect("").1;
        assert_eq!(
            serde_json::json!(v).to_string(),
            r#"{"intel_manager":{"intel":[[67,{"intel":10,"stale_intel":[]}]]}}"#
        );
    }

    #[test]
    fn test_parse_map_repeated_key() {
        assert_eq!(
            parse_value(r#"{x=1 x=1 y=1 y=2 z=1 z="asdf"}"#),
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
    fn test_deep_nested_object() {
        let test_depth = 250;
        let test_input = format!(
            "outer_key={}{}{}",
            "{".repeat(test_depth),
            "key=value",
            "}".repeat(test_depth)
        );
        // println!("{}", test_input);
        parse_file(test_input.as_str()).expect("Should parse");
    }

    #[test]
    fn test_nan_in_value() {
        // from a bug report for a save that failed to parse
        // narrowed down to this input
        // `parse_float` was grabbing the initial "nan"
        let test_input = "1=nano_shipyard";
        parse_file(test_input).expect("Should parse");
    }

    #[test]
    fn test_escaped_backslash() {
        // from a bug report for a save that failed to parse
        // narrowed down to mishandled escaped backslash at end of string
        let test_input = "prefix=\"GATE \\\\\"";
        parse_file(test_input).expect("Should parse");
    }

    #[test]
    fn test_skipped_key_in_mapping() {
        assert_eq!(
            parse_map_key_value_pair("key=other_key=value_1").expect("asdf"),
            ("", ("key", Value::Str("value_1")))
        );

        assert_eq!(
            parse_map_kv_list("key=other_key=value_2").expect("asdf"),
            ("", vec![("key", Value::Str("value_2"))])
        );

        // basic example:
        assert_eq!(
            parse_file("key=other_key=value"),
            Ok(Value::Map(
                HashMap::from([
                    ("key", Value::Str("value")),
                ])
            ))
        );
        // example found in real save:
        let save_content = r#"expired=yes
        event_id=					scope={
        type=none
        id=0
        random={ 0 3991148998 }
        }"#;
        assert_eq!(
            parse_file(save_content).unwrap(),
            Value::Map(
                HashMap::from([
                    ("expired", Value::Str("yes")),
                    ("event_id", Value::Map(
                        HashMap::from([
                            ("type", Value::Str("none")),
                            ("id", Value::Int(0)),
                            ("random", Value::List(vec![Value::Int(0), Value::Int(3991148998)])),
                        ])
                    )),
                ])
            )
        )
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
                        "required_dlcs",
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
                        "ship_names",
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

        // Bug report: parser does not handle escaped quotes
        assert_eq!(
            parse_file(
                r#"species_bio="Description contains a \"quoted\" word."
                   name_list="MAM2"
                   gender=not_set
                   trait="trait_resilient""#
            ).unwrap(),
            Value::Map(HashMap::from([
                ("species_bio", Value::Str(r#"Description contains a \"quoted\" word."#)),
                ("name_list", Value::Str("MAM2")),
                ("gender", Value::Str("not_set")),
                ("trait", Value::Str("trait_resilient")),
            ]))
        );

        assert_eq!(
            parse_file("color = rgb { 1 2 3 }").unwrap(),
            Value::Map(HashMap::from([
                ("color", Value::Color(("rgb", 1.0, 2.0, 3.0)))
            ]))
        )
    }
}