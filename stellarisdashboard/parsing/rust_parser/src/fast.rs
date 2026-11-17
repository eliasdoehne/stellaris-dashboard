//! Prototype of a faster save parser.
//!
//! A hand-written, single-pass, byte-level recursive-descent parser that accepts the same language
//! as the nom grammar in `parser.rs` and reports what it sees to a [`Sink`]:
//!
//! * [`PySink`] builds the Python objects directly (no intermediate tree), optionally sharing
//!   one `str` object between all occurrences of the same short string;
//! * [`TapeSink`] records a flat event tape, so that the parse can run without the GIL and the
//!   Python objects are built from the tape afterwards;
//! * [`ValueSink`] builds the legacy [`Value`] tree (used by the unit tests and to measure the
//!   effect of the lexer alone).
//!
//! An optional [`Filter`] makes the parser skip the values of keys that are not selected, at the
//! lexer level (brace matching), without reporting anything to the sink.
//!
//! The grammar quirks of the nom parser that are reproduced on purpose:
//!
//! * a `{ ... }` block is a map if its first token is followed by `=`, otherwise a list;
//!   `{}` is an empty list, `{ a= }` is an empty map;
//! * elements and pairs must be separated by whitespace (space, tab, CR, LF only);
//! * `key=` without a value is dropped (`a= b=1`, `a= }`, `a=` at the end of the file);
//! * values are classified in this order: date (`digits.digits.digits`, kept as a string),
//!   integer, float (also `nan` / `inf`), quoted string (escapes are kept verbatim), color
//!   (`rgb { 1 2 3 }`), unquoted string;
//! * repeated keys are collected into a list (see `merge` in [`PySink`]);
//! * inputs the nom parser rejects are rejected (e.g. `1.2.3x` and `1e` as values).

use std::collections::{HashMap, HashSet};
use std::hash::{BuildHasherDefault, Hasher};

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict};

use crate::parser::Value;

#[derive(Debug, PartialEq)]
pub struct ParseError {
    pub pos: usize,
    pub msg: &'static str,
}

type R = Result<(), ParseError>;

// ---------------------------------------------------------------------------------------------
// Hashing: FxHash-style multiplicative hash, much cheaper than SipHash for short keys.
// ---------------------------------------------------------------------------------------------

#[derive(Default, Clone, Copy)]
pub struct FxHasher(u64);

const FX_K: u64 = 0x517c_c1b7_2722_0a95;

impl FxHasher {
    #[inline(always)]
    fn add(&mut self, w: u64) {
        self.0 = (self.0.rotate_left(5) ^ w).wrapping_mul(FX_K);
    }
}

impl Hasher for FxHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        let mut b = bytes;
        while b.len() >= 8 {
            self.add(u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]));
            b = &b[8..];
        }
        if b.len() >= 4 {
            self.add(u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as u64);
            b = &b[4..];
        }
        for &x in b {
            self.add(x as u64);
        }
    }
    #[inline]
    fn write_usize(&mut self, i: usize) {
        self.add(i as u64);
    }
    #[inline]
    fn finish(&self) -> u64 {
        // hashbrown takes the control byte from the top bits, which the multiplication mixes best
        self.0
    }
}

type Fx = BuildHasherDefault<FxHasher>;

// ---------------------------------------------------------------------------------------------
// Projection filter
// ---------------------------------------------------------------------------------------------

/// Which parts of the document are reported to the sink.
pub enum Filter {
    /// Everything below this point.
    All,
    /// Only the listed keys of a map (each with its own filter); `default` applies to keys that
    /// are not listed (and to the elements of a list), `None` means such keys are skipped.
    Sel {
        keys: HashMap<Vec<u8>, Filter, Fx>,
        default: Option<Box<Filter>>,
    },
}

static ALL: Filter = Filter::All;

impl Filter {
    #[inline]
    fn child(&self, key: &[u8]) -> Option<&Filter> {
        match self {
            Filter::All => Some(&ALL),
            Filter::Sel { keys, default } => keys.get(key).or(default.as_deref()),
        }
    }

    #[inline]
    fn elem(&self) -> &Filter {
        match self {
            Filter::All => &ALL,
            Filter::Sel { default, .. } => default.as_deref().unwrap_or(&ALL),
        }
    }

    /// Build a filter from a Python description:
    /// `None` / `True` = everything; an iterable of names = only these keys (completely);
    /// a dict `{name: sub_filter}` = only these keys, filtered recursively, where the key `"*"`
    /// stands for every key that is not listed.
    pub fn from_py(obj: &Bound<'_, PyAny>) -> PyResult<Filter> {
        if obj.is_none() {
            return Ok(Filter::All);
        }
        if let Ok(b) = obj.extract::<bool>() {
            if b {
                return Ok(Filter::All);
            }
        }
        let mut keys: HashMap<Vec<u8>, Filter, Fx> = HashMap::default();
        let mut default = None;
        if let Ok(d) = obj.cast::<PyDict>() {
            for (k, v) in d.iter() {
                let name: String = k.str()?.extract()?;
                let sub = Filter::from_py(&v)?;
                if name == "*" {
                    default = Some(Box::new(sub));
                } else {
                    keys.insert(name.into_bytes(), sub);
                }
            }
        } else {
            for item in obj.try_iter()? {
                let name: String = item?.str()?.extract()?;
                keys.insert(name.into_bytes(), Filter::All);
            }
        }
        Ok(Filter::Sel { keys, default })
    }
}

// ---------------------------------------------------------------------------------------------
// Lexer tables and scalar classification
// ---------------------------------------------------------------------------------------------

const WS: u8 = 1; // nom's multispace: space, tab, CR, LF
const TOK: u8 = 2; // may be part of an unquoted string
const CHK: u8 = 4; // lead byte of a UTF-8 sequence that may encode a Unicode whitespace character

static CLASS: [u8; 256] = build_class();

const fn build_class() -> [u8; 256] {
    let mut t = [0u8; 256];
    let mut i = 0;
    while i < 256 {
        let b = i as u8;
        t[i] = if b == b' ' || b == b'\t' || b == b'\r' || b == b'\n' {
            WS
        } else if b == 0x0b || b == 0x0c {
            // whitespace for char::is_whitespace (ends an unquoted string) but not for multispace
            0
        } else if matches!(
            b,
            b'"' | b'=' | b'{' | b'}' | b'<' | b'>' | b'[' | b']' | b'#' | b'$' | b'|'
        ) {
            0
        } else if b == 0xC2 || b == 0xE1 || b == 0xE2 || b == 0xE3 {
            // U+0085, U+00A0, U+1680, U+2000..U+200A, U+2028, U+2029, U+202F, U+205F, U+3000
            TOK | CHK
        } else {
            TOK
        };
        i += 1;
    }
    t
}

#[derive(Debug, PartialEq)]
enum Scalar {
    Str,
    Int(i64),
    Float(f64),
    /// `1.2.3x`: the nom parser takes the date prefix and then fails on the rest
    PartialDate,
    /// `1e`, `1.5e+x`: nom's `cut` after the exponent marker aborts the whole parse
    Failure,
}

#[inline]
fn digits(tok: &[u8], mut i: usize) -> usize {
    while i < tok.len() && tok[i].is_ascii_digit() {
        i += 1;
    }
    i
}

/// nom 7 `recognize_float_or_exceptions`: length of the recognised prefix.
/// `Err(())` is nom's `Failure` (exponent marker without digits).
fn recognize_float(tok: &[u8]) -> Result<Option<usize>, ()> {
    let mut i = 0;
    if i < tok.len() && (tok[i] == b'+' || tok[i] == b'-') {
        i += 1;
    }
    let d = digits(tok, i);
    let mut matched = false;
    if d > i {
        i = d;
        if i < tok.len() && tok[i] == b'.' {
            i = digits(tok, i + 1);
        }
        matched = true;
    } else if i < tok.len() && tok[i] == b'.' {
        let d2 = digits(tok, i + 1);
        if d2 > i + 1 {
            i = d2;
            matched = true;
        }
    }
    if matched {
        if i < tok.len() && (tok[i] == b'e' || tok[i] == b'E') {
            let mut j = i + 1;
            if j < tok.len() && (tok[j] == b'+' || tok[j] == b'-') {
                j += 1;
            }
            let d3 = digits(tok, j);
            if d3 == j {
                return Err(());
            }
            i = d3;
        }
        return Ok(Some(i));
    }
    if tok.len() >= 3 {
        let p = &tok[..3];
        if p.eq_ignore_ascii_case(b"nan") || p.eq_ignore_ascii_case(b"inf") {
            return Ok(Some(3));
        }
    }
    Ok(None)
}

#[inline]
fn as_str(b: &[u8]) -> &str {
    // SAFETY: the parser only runs on input that was validated as UTF-8, and tokens are cut at
    // ASCII delimiters or at character boundaries.
    unsafe { std::str::from_utf8_unchecked(b) }
}

/// Classify an unquoted value token the way the nom `alt` chain does.
#[inline]
fn classify(tok: &[u8]) -> Scalar {
    let b0 = tok[0];
    if b0.is_ascii_digit() || b0 == b'-' {
        // fast path: plain integers that cannot overflow
        let start = (b0 == b'-') as usize;
        let n = tok.len() - start;
        if n >= 1 && n <= 18 {
            let mut v: i64 = 0;
            let mut ok = true;
            for &c in &tok[start..] {
                let d = c.wrapping_sub(b'0');
                if d > 9 {
                    ok = false;
                    break;
                }
                v = v * 10 + d as i64;
            }
            if ok {
                return Scalar::Int(if start == 1 { -v } else { v });
            }
        }
    } else if !matches!(b0, b'+' | b'.' | b'n' | b'N' | b'i' | b'I') {
        return Scalar::Str;
    }
    classify_slow(tok)
}

fn classify_slow(tok: &[u8]) -> Scalar {
    // date: digits* '.' digits* '.' digits*
    let i = digits(tok, 0);
    if i < tok.len() && tok[i] == b'.' {
        let i = digits(tok, i + 1);
        if i < tok.len() && tok[i] == b'.' {
            let i = digits(tok, i + 1);
            return if i == tok.len() { Scalar::Str } else { Scalar::PartialDate };
        }
    }
    match recognize_float(tok) {
        Err(()) => Scalar::Failure,
        Ok(Some(n)) if n == tok.len() => {
            let s = as_str(tok);
            if let Ok(v) = s.parse::<i64>() {
                Scalar::Int(v)
            } else if let Ok(v) = s.parse::<f64>() {
                Scalar::Float(v)
            } else {
                Scalar::Str
            }
        }
        Ok(_) => Scalar::Str,
    }
}

// ---------------------------------------------------------------------------------------------
// Sink
// ---------------------------------------------------------------------------------------------

pub trait Sink<'a> {
    fn begin_list(&mut self) -> R;
    fn end_list(&mut self) -> R;
    fn begin_map(&mut self) -> R;
    fn end_map(&mut self) -> R;
    /// The key of the next value in the current map. Only called when a value follows.
    fn key(&mut self, k: &'a [u8]) -> R;
    fn string(&mut self, s: &'a [u8]) -> R;
    fn int(&mut self, v: i64) -> R;
    fn float(&mut self, v: f64) -> R;
    fn color(&mut self, space: &'a [u8], c: [f64; 3]) -> R;
    /// Called with the input offset just before `end_list` / `end_map` (for statistics).
    #[inline(always)]
    fn position(&mut self, _pos: usize) {}
}

// ---------------------------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------------------------

const MAX_DEPTH: usize = 500;

pub struct Parser<'a, 's, S: Sink<'a>> {
    buf: &'a [u8],
    pos: usize,
    depth: usize,
    sink: &'s mut S,
}

/// Parse a whole document (the body of a map without the braces) into `sink`.
/// `input` must be valid UTF-8.
pub fn parse_document<'a, S: Sink<'a>>(input: &'a [u8], filter: &Filter, sink: &mut S) -> R {
    let mut p = Parser { buf: input, pos: 0, depth: 0, sink };
    p.sink.begin_map()?;
    p.skip_ws();
    p.parse_pairs(filter, None)?;
    if p.pos < p.buf.len() {
        // stopped at a `}`; the nom parser accepts trailing Unicode whitespace only
        if !as_str(&p.buf[p.pos..]).chars().all(char::is_whitespace) {
            return p.err("unexpected input after the last key");
        }
    }
    p.sink.position(p.buf.len());
    p.sink.end_map()
}

impl<'a, 's, S: Sink<'a>> Parser<'a, 's, S> {
    #[cold]
    fn err<T>(&self, msg: &'static str) -> Result<T, ParseError> {
        Err(ParseError { pos: self.pos, msg })
    }

    #[cold]
    fn rest_is_whitespace(&self) -> bool {
        as_str(&self.buf[self.pos..]).chars().all(char::is_whitespace)
    }

    /// Next byte, or 0 at the end of the input (0 is never a delimiter we act on).
    #[inline(always)]
    fn peek(&self) -> u8 {
        if self.pos < self.buf.len() {
            self.buf[self.pos]
        } else {
            0
        }
    }

    #[inline(always)]
    fn at_end(&self) -> bool {
        self.pos >= self.buf.len()
    }

    /// Skip whitespace; returns whether there was any.
    #[inline(always)]
    fn skip_ws(&mut self) -> bool {
        let start = self.pos;
        let buf = self.buf;
        let mut p = start;
        while p < buf.len() && CLASS[buf[p] as usize] == WS {
            p += 1;
        }
        self.pos = p;
        p > start
    }

    /// The longest run of unquoted-string characters at the current position (may be empty).
    #[inline(always)]
    fn scan_token(&mut self) -> &'a [u8] {
        let buf = self.buf;
        let start = self.pos;
        let mut p = start;
        loop {
            while p < buf.len() && CLASS[buf[p] as usize] == TOK {
                p += 1;
            }
            if p < buf.len() && CLASS[buf[p] as usize] & CHK != 0 {
                let c = as_str(&buf[p..]).chars().next().unwrap();
                if !c.is_whitespace() {
                    p += c.len_utf8();
                    continue;
                }
            }
            break;
        }
        self.pos = p;
        &buf[start..p]
    }

    /// A quoted string; the current position is the opening quote. Returns the raw content.
    #[inline(always)]
    fn scan_quoted(&mut self) -> Result<&'a [u8], ParseError> {
        let buf = self.buf;
        let start = self.pos + 1;
        let mut p = start;
        loop {
            if p >= buf.len() {
                return self.err("unterminated string");
            }
            let b = buf[p];
            if b == b'"' {
                break;
            }
            if b == b'\\' {
                if p + 1 >= buf.len() || (buf[p + 1] != b'"' && buf[p + 1] != b'\\') {
                    self.pos = p;
                    return self.err("invalid escape sequence in string");
                }
                p += 2;
            } else {
                p += 1;
            }
        }
        self.pos = p + 1;
        Ok(&buf[start..p])
    }

    /// Skip a `{ ... }` block without looking at its contents (current position: the `{`).
    fn skip_block(&mut self) -> R {
        let buf = self.buf;
        let mut p = self.pos + 1;
        let mut depth = 1usize;
        while p < buf.len() {
            match buf[p] {
                b'{' => depth += 1,
                b'}' => {
                    depth -= 1;
                    if depth == 0 {
                        self.pos = p + 1;
                        return Ok(());
                    }
                }
                b'"' => {
                    p += 1;
                    while p < buf.len() && buf[p] != b'"' {
                        if buf[p] == b'\\' {
                            p += 1;
                        }
                        p += 1;
                    }
                }
                _ => {}
            }
            p += 1;
        }
        self.pos = buf.len();
        self.err("unterminated block")
    }

    /// `rgb { 1 2 3 }`; the current position is just after the `rgb` / `hsv` token.
    /// Leaves the position unchanged if this is not a color.
    fn try_color(&mut self) -> Result<Option<[f64; 3]>, ParseError> {
        let save = self.pos;
        self.skip_ws();
        if self.peek() != b'{' {
            self.pos = save;
            return Ok(None);
        }
        self.pos += 1;
        let mut c = [0f64; 3];
        for slot in c.iter_mut() {
            self.skip_ws();
            let tok = self.scan_token();
            let v = match recognize_float(tok) {
                Err(()) => return self.err("malformed number"),
                Ok(Some(n)) if n == tok.len() && n > 0 => as_str(tok).parse::<f64>().ok(),
                Ok(_) => None,
            };
            match v {
                Some(v) => *slot = v,
                None => {
                    self.pos = save;
                    return Ok(None);
                }
            }
        }
        self.skip_ws();
        if self.peek() != b'}' {
            self.pos = save;
            return Ok(None);
        }
        self.pos += 1;
        Ok(Some(c))
    }

    /// Report an unquoted value token (already consumed) to the sink.
    #[inline(always)]
    fn emit_token(&mut self, tok: &'a [u8], class: Scalar) -> R {
        match class {
            Scalar::Int(v) => self.sink.int(v),
            Scalar::Float(v) => self.sink.float(v),
            Scalar::Str => {
                if tok.len() == 3 && (tok == b"rgb" || tok == b"hsv") {
                    if let Some(c) = self.try_color()? {
                        return self.sink.color(tok, c);
                    }
                }
                self.sink.string(tok)
            }
            Scalar::PartialDate => self.err("malformed date"),
            Scalar::Failure => self.err("malformed number"),
        }
    }

    /// Parse `key=value` pairs up to (not including) the closing `}` or the end of the input.
    /// `first`: a key whose `=` has been consumed already; otherwise the position is at a key.
    fn parse_pairs(&mut self, filter: &Filter, first: Option<&'a [u8]>) -> R {
        let mut pending = first;
        loop {
            let mut key = match pending.take() {
                Some(k) => k,
                None => {
                    let k = match self.peek() {
                        b'"' => self.scan_quoted()?,
                        _ => {
                            let t = self.scan_token();
                            if t.is_empty() {
                                return self.err("expected a key");
                            }
                            t
                        }
                    };
                    self.skip_ws();
                    if self.peek() != b'=' {
                        return self.err("expected '=' after key");
                    }
                    self.pos += 1;
                    k
                }
            };
            // The value of `key`. Loops when the "value" turns out to be the next key
            // (`valueless_key= next_key=value`).
            let ws_after = loop {
                let sub = filter.child(key);
                let had_ws = self.skip_ws();
                let c = self.peek();
                if c == b'{' {
                    match sub {
                        Some(f) => {
                            self.sink.key(key)?;
                            self.parse_block(f)?;
                        }
                        None => self.skip_block()?,
                    }
                    let ws = self.skip_ws();
                    if self.peek() == b'=' {
                        return self.err("unexpected '=' after a block");
                    }
                    break ws;
                } else if c == b'"' {
                    let s = self.scan_quoted()?;
                    let ws = self.skip_ws();
                    if self.peek() == b'=' {
                        if !had_ws {
                            return self.err("expected whitespace before key");
                        }
                        self.pos += 1;
                        key = s;
                        continue;
                    }
                    if sub.is_some() {
                        self.sink.key(key)?;
                        self.sink.string(s)?;
                    }
                    break ws;
                } else if CLASS[c as usize] & TOK != 0 && !self.at_end() {
                    let tok = self.scan_token();
                    if tok.is_empty() {
                        // Unicode whitespace: not a value
                        break false;
                    }
                    if sub.is_none() {
                        // skipped: no need to classify the token
                        let after = self.pos;
                        let ws = self.skip_ws();
                        if self.peek() == b'=' {
                            if !had_ws {
                                return self.err("expected whitespace before key");
                            }
                            self.pos += 1;
                            key = tok;
                            continue;
                        }
                        if tok.len() == 3 && (tok == b"rgb" || tok == b"hsv") {
                            self.pos = after;
                            self.try_color()?;
                            break self.skip_ws();
                        }
                        break ws;
                    }
                    let class = classify(tok);
                    if matches!(class, Scalar::PartialDate | Scalar::Failure) {
                        return self.err("malformed value");
                    }
                    let after = self.pos;
                    let ws = self.skip_ws();
                    if self.peek() == b'=' {
                        if !had_ws {
                            return self.err("expected whitespace before key");
                        }
                        self.pos += 1;
                        key = tok;
                        continue;
                    }
                    self.sink.key(key)?;
                    if class == Scalar::Str && tok.len() == 3 && (tok == b"rgb" || tok == b"hsv") {
                        // possibly a color, which continues after the token
                        self.pos = after;
                        self.emit_token(tok, class)?;
                        let ws = self.skip_ws();
                        if self.peek() == b'=' {
                            return self.err("unexpected '=' after a color");
                        }
                        break ws;
                    }
                    self.emit_token(tok, class)?;
                    break ws;
                } else {
                    // `}`, end of input, or something that is not a value: the key has no value
                    break had_ws;
                }
            };
            let c = self.peek();
            if self.at_end() || c == b'}' {
                return Ok(());
            }
            if CLASS[c as usize] != TOK && c != b'"' && self.depth == 0 && self.rest_is_whitespace() {
                // the nom parser accepts trailing Unicode whitespace at the end of the document
                self.pos = self.buf.len();
                return Ok(());
            }
            if !ws_after {
                return self.err("expected whitespace between key-value pairs");
            }
        }
    }

    /// Parse a `{ ... }` block; the current position is the `{`.
    fn parse_block(&mut self, filter: &Filter) -> R {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return self.err("nesting too deep");
        }
        self.pos += 1;
        self.skip_ws();
        let c = self.peek();
        let mut first_token: Option<(&'a [u8], Scalar)> = None;
        if c == b'"' {
            let p0 = self.pos;
            let key = self.scan_quoted()?;
            self.skip_ws();
            if self.peek() == b'=' {
                self.pos += 1;
                return self.finish_map(filter, key);
            }
            self.pos = p0;
        } else if CLASS[c as usize] & TOK != 0 && !self.at_end() {
            let tok = self.scan_token();
            if tok.is_empty() {
                return self.err("unexpected character in block");
            }
            let class = classify(tok);
            if class == Scalar::Failure {
                // the nom parser tries the block as a list first and aborts on this token
                return self.err("malformed number");
            }
            let after = self.pos;
            self.skip_ws();
            if self.peek() == b'=' {
                self.pos += 1;
                return self.finish_map(filter, tok);
            }
            self.pos = after;
            first_token = Some((tok, class));
        }
        // a list
        let elem_filter = filter.elem();
        self.sink.begin_list()?;
        if let Some((tok, class)) = first_token {
            self.emit_token(tok, class)?;
        } else if self.peek() == b'}' {
            self.pos += 1;
            self.depth -= 1;
            return self.end_list_at();
        } else {
            self.parse_list_element(elem_filter)?;
        }
        loop {
            let ws = self.skip_ws();
            if self.peek() == b'}' {
                self.pos += 1;
                self.depth -= 1;
                return self.end_list_at();
            }
            if !ws {
                return self.err("expected whitespace or '}' in list");
            }
            self.parse_list_element(elem_filter)?;
        }
    }

    #[inline(always)]
    fn parse_list_element(&mut self, filter: &Filter) -> R {
        let c = self.peek();
        if c == b'{' {
            self.parse_block(filter)
        } else if c == b'"' {
            let s = self.scan_quoted()?;
            self.sink.string(s)
        } else {
            let tok = self.scan_token();
            if tok.is_empty() {
                return self.err("expected a value or '}' in list");
            }
            self.emit_token(tok, classify(tok))
        }
    }

    fn finish_map(&mut self, filter: &Filter, first_key: &'a [u8]) -> R {
        self.sink.begin_map()?;
        self.parse_pairs(filter, Some(first_key))?;
        if self.peek() != b'}' {
            return self.err("unterminated map");
        }
        self.pos += 1;
        self.depth -= 1;
        self.sink.position(self.pos);
        self.sink.end_map()
    }

    #[inline(always)]
    fn end_list_at(&mut self) -> R {
        self.sink.position(self.pos);
        self.sink.end_list()
    }
}

// ---------------------------------------------------------------------------------------------
// Sink that validates only
// ---------------------------------------------------------------------------------------------

pub struct NullSink;

impl<'a> Sink<'a> for NullSink {
    fn begin_list(&mut self) -> R { Ok(()) }
    fn end_list(&mut self) -> R { Ok(()) }
    fn begin_map(&mut self) -> R { Ok(()) }
    fn end_map(&mut self) -> R { Ok(()) }
    fn key(&mut self, _k: &'a [u8]) -> R { Ok(()) }
    fn string(&mut self, _s: &'a [u8]) -> R { Ok(()) }
    fn int(&mut self, _v: i64) -> R { Ok(()) }
    fn float(&mut self, _v: f64) -> R { Ok(()) }
    fn color(&mut self, _space: &'a [u8], _c: [f64; 3]) -> R { Ok(()) }
}

// ---------------------------------------------------------------------------------------------
// Sink that measures how many bytes / values each key path accounts for
// ---------------------------------------------------------------------------------------------

#[derive(Default, Clone, Copy)]
pub struct PathStats {
    /// number of occurrences of this path
    pub count: u64,
    /// bytes of input from the start of the key to the start of the next key / end of the parent
    pub bytes: u64,
    /// values (scalars and containers) at or below this path
    pub values: u64,
}

struct StatsFrame {
    /// length of `path` up to and including this frame's own path
    path_len: usize,
    /// length of `path` before this frame was opened
    restore_len: usize,
    /// whether keys of this frame are tracked as separate paths
    tracked: bool,
    /// the currently open child: (start offset, value counter at its start)
    open: Option<(usize, u64)>,
}

/// Paths are `/`-separated keys; integer keys are written as `*`, list levels as `[]`.
pub struct StatsSink {
    base: usize,
    max_depth: usize,
    path: String,
    frames: Vec<StatsFrame>,
    values: u64,
    last_pos: usize,
    pub stats: HashMap<String, PathStats>,
}

impl StatsSink {
    pub fn new(input: &[u8], max_depth: usize) -> Self {
        StatsSink {
            base: input.as_ptr() as usize,
            max_depth,
            path: String::new(),
            frames: Vec::new(),
            values: 0,
            last_pos: 0,
            stats: HashMap::new(),
        }
    }

    pub fn total_values(&self) -> u64 {
        self.values
    }

    fn close_child(&mut self, end: usize) {
        let values = self.values;
        if let Some(f) = self.frames.last_mut() {
            if let Some((start, v0)) = f.open.take() {
                let s = self.stats.entry(self.path.clone()).or_default();
                s.count += 1;
                s.bytes += (end - start) as u64;
                s.values += values - v0;
                self.path.truncate(f.path_len);
            }
        }
    }

    fn begin(&mut self, is_list: bool) {
        self.values += 1;
        let restore_len = self.path.len();
        if is_list && !self.frames.is_empty() {
            self.path.push_str("/[]");
        }
        // number of path components so far decides whether deeper keys are tracked
        let depth = self.path.matches('/').count();
        self.frames.push(StatsFrame {
            path_len: self.path.len(),
            restore_len,
            tracked: !is_list && depth < self.max_depth,
            open: None,
        });
    }

    fn end(&mut self) {
        self.close_child(self.last_pos);
        if let Some(f) = self.frames.pop() {
            self.path.truncate(f.restore_len);
        }
    }
}

impl<'a> Sink<'a> for StatsSink {
    fn begin_list(&mut self) -> R { self.begin(true); Ok(()) }
    fn end_list(&mut self) -> R { self.end(); Ok(()) }
    fn begin_map(&mut self) -> R { self.begin(false); Ok(()) }
    fn end_map(&mut self) -> R { self.end(); Ok(()) }
    fn key(&mut self, k: &'a [u8]) -> R {
        let tracked = self.frames.last().map_or(false, |f| f.tracked);
        if tracked {
            let start = k.as_ptr() as usize - self.base;
            self.close_child(start);
            self.path.push('/');
            if as_str(k).parse::<i64>().is_ok() {
                self.path.push('*');
            } else {
                self.path.push_str(as_str(k));
            }
            let values = self.values;
            self.frames.last_mut().unwrap().open = Some((start, values));
        }
        Ok(())
    }
    fn string(&mut self, _s: &'a [u8]) -> R { self.values += 1; Ok(()) }
    fn int(&mut self, _v: i64) -> R { self.values += 1; Ok(()) }
    fn float(&mut self, _v: f64) -> R { self.values += 1; Ok(()) }
    fn color(&mut self, _space: &'a [u8], _c: [f64; 3]) -> R { self.values += 5; Ok(()) }
    fn position(&mut self, pos: usize) { self.last_pos = pos; }
}

// ---------------------------------------------------------------------------------------------
// Sink that builds the legacy `Value` tree
// ---------------------------------------------------------------------------------------------

enum Building<'a> {
    List(Vec<Value<'a>>),
    Map(HashMap<&'a str, Value<'a>>, HashSet<&'a str>, &'a str),
}

#[derive(Default)]
pub struct ValueSink<'a> {
    stack: Vec<Building<'a>>,
    pub result: Option<Value<'a>>,
}

impl<'a> ValueSink<'a> {
    fn push(&mut self, value: Value<'a>) -> R {
        match self.stack.last_mut() {
            None => self.result = Some(value),
            Some(Building::List(v)) => v.push(value),
            Some(Building::Map(hm, handled, key)) => {
                let key = *key;
                // same merge rules as `parse_map_inner` in parser.rs
                match hm.remove(key) {
                    None => {
                        hm.insert(key, value);
                    }
                    Some(Value::List(mut vec)) => {
                        let merged = match (value, handled.contains(key)) {
                            (Value::List(new_vec), false) => {
                                handled.insert(key);
                                Value::List(vec![Value::List(vec), Value::List(new_vec)])
                            }
                            (value, _) => {
                                vec.push(value);
                                Value::List(vec)
                            }
                        };
                        hm.insert(key, merged);
                    }
                    Some(other) => {
                        hm.insert(key, Value::List(vec![other, value]));
                    }
                }
            }
        }
        Ok(())
    }
}

impl<'a> Sink<'a> for ValueSink<'a> {
    fn begin_list(&mut self) -> R {
        self.stack.push(Building::List(Vec::new()));
        Ok(())
    }
    fn end_list(&mut self) -> R {
        match self.stack.pop() {
            Some(Building::List(v)) => self.push(Value::List(v)),
            _ => unreachable!(),
        }
    }
    fn begin_map(&mut self) -> R {
        self.stack.push(Building::Map(HashMap::new(), HashSet::new(), ""));
        Ok(())
    }
    fn end_map(&mut self) -> R {
        match self.stack.pop() {
            Some(Building::Map(hm, _, _)) => self.push(Value::Map(hm)),
            _ => unreachable!(),
        }
    }
    fn key(&mut self, k: &'a [u8]) -> R {
        if let Some(Building::Map(_, _, key)) = self.stack.last_mut() {
            *key = as_str(k);
        }
        Ok(())
    }
    fn string(&mut self, s: &'a [u8]) -> R { self.push(Value::Str(as_str(s))) }
    fn int(&mut self, v: i64) -> R { self.push(Value::Int(v)) }
    fn float(&mut self, v: f64) -> R { self.push(Value::Float(v)) }
    fn color(&mut self, space: &'a [u8], c: [f64; 3]) -> R {
        self.push(Value::Color((as_str(space), c[0], c[1], c[2])))
    }
}

/// Parse with the new parser into the legacy `Value` tree.
pub fn parse_to_value<'a>(input: &'a str, filter: &Filter) -> Result<Value<'a>, ParseError> {
    let mut sink = ValueSink::default();
    parse_document(input.as_bytes(), filter, &mut sink)?;
    Ok(sink.result.take().unwrap())
}

// ---------------------------------------------------------------------------------------------
// Event tape (parse without the GIL, build Python objects later)
// ---------------------------------------------------------------------------------------------

#[derive(Clone, Copy)]
pub enum Ev {
    Key(u32, u32),
    Str(u32, u32),
    Int(i64),
    Float(f64),
    /// offset of the 3-byte color space name; followed by three `Float` events
    Color(u32),
    BeginList,
    EndList,
    BeginMap,
    EndMap,
}

pub struct TapeSink {
    base: usize,
    pub events: Vec<Ev>,
}

impl TapeSink {
    pub fn new(input: &[u8]) -> TapeSink {
        assert!(input.len() < u32::MAX as usize, "input too large for the event tape");
        TapeSink { base: input.as_ptr() as usize, events: Vec::with_capacity(input.len() / 6) }
    }
    #[inline(always)]
    fn off(&self, s: &[u8]) -> (u32, u32) {
        ((s.as_ptr() as usize - self.base) as u32, s.len() as u32)
    }
}

impl<'a> Sink<'a> for TapeSink {
    #[inline(always)]
    fn begin_list(&mut self) -> R { self.events.push(Ev::BeginList); Ok(()) }
    #[inline(always)]
    fn end_list(&mut self) -> R { self.events.push(Ev::EndList); Ok(()) }
    #[inline(always)]
    fn begin_map(&mut self) -> R { self.events.push(Ev::BeginMap); Ok(()) }
    #[inline(always)]
    fn end_map(&mut self) -> R { self.events.push(Ev::EndMap); Ok(()) }
    #[inline(always)]
    fn key(&mut self, k: &'a [u8]) -> R {
        let (o, l) = self.off(k);
        self.events.push(Ev::Key(o, l));
        Ok(())
    }
    #[inline(always)]
    fn string(&mut self, s: &'a [u8]) -> R {
        let (o, l) = self.off(s);
        self.events.push(Ev::Str(o, l));
        Ok(())
    }
    #[inline(always)]
    fn int(&mut self, v: i64) -> R { self.events.push(Ev::Int(v)); Ok(()) }
    #[inline(always)]
    fn float(&mut self, v: f64) -> R { self.events.push(Ev::Float(v)); Ok(()) }
    fn color(&mut self, space: &'a [u8], c: [f64; 3]) -> R {
        let (o, _) = self.off(space);
        self.events.push(Ev::Color(o));
        for v in c {
            self.events.push(Ev::Float(v));
        }
        Ok(())
    }
}

/// Feed a recorded tape to another sink.
pub fn replay<'a, S: Sink<'a>>(input: &'a [u8], events: &[Ev], sink: &mut S) -> R {
    let mut i = 0;
    while i < events.len() {
        match events[i] {
            Ev::Key(o, l) => sink.key(&input[o as usize..(o + l) as usize])?,
            Ev::Str(o, l) => sink.string(&input[o as usize..(o + l) as usize])?,
            Ev::Int(v) => sink.int(v)?,
            Ev::Float(v) => sink.float(v)?,
            Ev::Color(o) => {
                let mut c = [0f64; 3];
                for (k, slot) in c.iter_mut().enumerate() {
                    if let Ev::Float(v) = events[i + 1 + k] {
                        *slot = v;
                    }
                }
                sink.color(&input[o as usize..o as usize + 3], c)?;
                i += 3;
            }
            Ev::BeginList => sink.begin_list()?,
            Ev::EndList => sink.end_list()?,
            Ev::BeginMap => sink.begin_map()?,
            Ev::EndMap => sink.end_map()?,
        }
        i += 1;
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Sink that builds Python objects
// ---------------------------------------------------------------------------------------------

struct Frame<'a> {
    /// null for a list
    dict: *mut ffi::PyObject,
    /// list: index of its first element in `vals`
    start: usize,
    /// map: the key of the next value (owned reference) and its raw text
    key: *mut ffi::PyObject,
    raw_key: &'a [u8],
    /// map: keys whose repeated list values have been wrapped into a list of lists already
    handled: Option<Box<HashSet<&'a [u8], Fx>>>,
}

/// Strings up to this length are looked up in / added to the string cache.
const CACHE_MAX_LEN: usize = 48;
/// Stop adding to the cache at this many distinct strings.
const CACHE_MAX_ENTRIES: usize = 1 << 20;

pub struct PySink<'py, 'a> {
    _py: Python<'py>,
    vals: Vec<*mut ffi::PyObject>,
    frames: Vec<Frame<'a>>,
    cache: HashMap<&'a [u8], *mut ffi::PyObject, Fx>,
    use_cache: bool,
    /// Remove the finished lists and dicts from the cyclic garbage collector's bookkeeping
    /// (see `parse_save_file_fast`).
    untrack: bool,
    result: *mut ffi::PyObject,
    pub cache_hits: u64,
    pub cache_misses: u64,
}

const PY_ERR: ParseError = ParseError { pos: 0, msg: "Python error while building the result" };

impl<'py, 'a> PySink<'py, 'a> {
    pub fn new(py: Python<'py>, use_cache: bool, untrack: bool) -> Self {
        PySink {
            _py: py,
            vals: Vec::with_capacity(1024),
            frames: Vec::with_capacity(64),
            cache: HashMap::default(),
            use_cache,
            untrack,
            result: std::ptr::null_mut(),
            cache_hits: 0,
            cache_misses: 0,
        }
    }

    /// The finished object (after a successful parse).
    pub fn take_result(&mut self) -> Option<Py<PyAny>> {
        if self.result.is_null() {
            return None;
        }
        let r = std::mem::replace(&mut self.result, std::ptr::null_mut());
        // SAFETY: `result` is an owned reference
        Some(unsafe { Bound::from_owned_ptr(self._py, r) }.unbind())
    }

    #[inline(always)]
    fn new_str(s: &[u8]) -> Result<*mut ffi::PyObject, ParseError> {
        // SAFETY: GIL is held (we have a Python token), `s` is valid UTF-8
        let o = unsafe {
            ffi::PyUnicode_FromStringAndSize(s.as_ptr() as *const _, s.len() as ffi::Py_ssize_t)
        };
        if o.is_null() { Err(PY_ERR) } else { Ok(o) }
    }

    /// A new reference to a `str` with this content.
    #[inline(always)]
    fn make_str(&mut self, s: &'a [u8]) -> Result<*mut ffi::PyObject, ParseError> {
        if !self.use_cache || s.len() > CACHE_MAX_LEN {
            return Self::new_str(s);
        }
        if let Some(&o) = self.cache.get(s) {
            self.cache_hits += 1;
            unsafe { ffi::Py_INCREF(o) };
            return Ok(o);
        }
        self.cache_misses += 1;
        let o = Self::new_str(s)?;
        if self.cache.len() < CACHE_MAX_ENTRIES {
            unsafe { ffi::Py_INCREF(o) };
            self.cache.insert(s, o);
        }
        Ok(o)
    }

    #[inline(always)]
    fn push(&mut self, v: *mut ffi::PyObject) -> R {
        if v.is_null() {
            return Err(PY_ERR);
        }
        match self.frames.last_mut() {
            None => {
                self.result = v;
                Ok(())
            }
            Some(f) if f.dict.is_null() => {
                self.vals.push(v);
                Ok(())
            }
            Some(f) => unsafe { Self::insert(f, v) },
        }
    }

    /// Insert `v` (owned) under the frame's pending key, merging repeated keys like the legacy parser.
    #[inline(always)]
    unsafe fn insert(f: &mut Frame<'a>, v: *mut ffi::PyObject) -> R {
        let key = std::mem::replace(&mut f.key, std::ptr::null_mut());
        debug_assert!(!key.is_null());
        let r = ffi::PyDict_SetDefaultRef(f.dict, key, v, std::ptr::null_mut());
        let res = if r == 0 {
            ffi::Py_DECREF(v);
            Ok(())
        } else if r == 1 {
            Self::merge(f, key, v)
        } else {
            ffi::Py_DECREF(v);
            Err(PY_ERR)
        };
        ffi::Py_DECREF(key);
        res
    }

    #[cold]
    #[inline(never)]
    unsafe fn merge(f: &mut Frame<'a>, key: *mut ffi::PyObject, v: *mut ffi::PyObject) -> R {
        let old = ffi::PyDict_GetItemWithError(f.dict, key); // borrowed
        if old.is_null() {
            ffi::Py_DECREF(v);
            return Err(PY_ERR);
        }
        if ffi::PyList_CheckExact(old) != 0 {
            let handled = f.handled.get_or_insert_with(Default::default);
            if ffi::PyList_CheckExact(v) != 0 && !handled.contains(f.raw_key) {
                handled.insert(f.raw_key);
                // fall through: wrap both lists
            } else {
                let rc = ffi::PyList_Append(old, v);
                ffi::Py_DECREF(v);
                return if rc == 0 { Ok(()) } else { Err(PY_ERR) };
            }
        }
        let pair = ffi::PyList_New(2);
        if pair.is_null() {
            ffi::Py_DECREF(v);
            return Err(PY_ERR);
        }
        ffi::Py_INCREF(old);
        ffi::PyList_SET_ITEM(pair, 0, old);
        ffi::PyList_SET_ITEM(pair, 1, v);
        let rc = ffi::PyDict_SetItem(f.dict, key, pair);
        ffi::Py_DECREF(pair);
        if rc == 0 { Ok(()) } else { Err(PY_ERR) }
    }
}

impl<'py, 'a> Drop for PySink<'py, 'a> {
    fn drop(&mut self) {
        unsafe {
            for &v in &self.vals {
                ffi::Py_DECREF(v);
            }
            for f in &self.frames {
                ffi::Py_XDECREF(f.dict);
                ffi::Py_XDECREF(f.key);
            }
            for (_, &v) in self.cache.iter() {
                ffi::Py_DECREF(v);
            }
            ffi::Py_XDECREF(self.result);
        }
    }
}

impl<'py, 'a> Sink<'a> for PySink<'py, 'a> {
    #[inline(always)]
    fn begin_list(&mut self) -> R {
        self.frames.push(Frame {
            dict: std::ptr::null_mut(),
            start: self.vals.len(),
            key: std::ptr::null_mut(),
            raw_key: b"",
            handled: None,
        });
        Ok(())
    }

    #[inline(always)]
    fn end_list(&mut self) -> R {
        let f = self.frames.pop().unwrap();
        let n = self.vals.len() - f.start;
        let list = unsafe { ffi::PyList_New(n as ffi::Py_ssize_t) };
        if list.is_null() {
            return Err(PY_ERR);
        }
        for (i, &v) in self.vals[f.start..].iter().enumerate() {
            unsafe { ffi::PyList_SET_ITEM(list, i as ffi::Py_ssize_t, v) };
        }
        self.vals.truncate(f.start);
        if self.untrack {
            unsafe { ffi::PyObject_GC_UnTrack(list as *mut _) };
        }
        self.push(list)
    }

    #[inline(always)]
    fn begin_map(&mut self) -> R {
        let dict = unsafe { ffi::PyDict_New() };
        if dict.is_null() {
            return Err(PY_ERR);
        }
        self.frames.push(Frame {
            dict,
            start: 0,
            key: std::ptr::null_mut(),
            raw_key: b"",
            handled: None,
        });
        Ok(())
    }

    #[inline(always)]
    fn end_map(&mut self) -> R {
        let f = self.frames.pop().unwrap();
        debug_assert!(f.key.is_null());
        if self.untrack {
            unsafe { ffi::PyObject_GC_UnTrack(f.dict as *mut _) };
        }
        self.push(f.dict)
    }

    #[inline(always)]
    fn key(&mut self, k: &'a [u8]) -> R {
        // Like the legacy conversion: keys that parse as an i64 become Python ints.
        let mut obj = std::ptr::null_mut();
        if let Some(&b0) = k.first() {
            if b0.is_ascii_digit() || b0 == b'-' || b0 == b'+' {
                // classify() yields Int exactly when `str::parse::<i64>` succeeds
                if let Scalar::Int(v) = classify(k) {
                    obj = unsafe { ffi::PyLong_FromLongLong(v) };
                    if obj.is_null() {
                        return Err(PY_ERR);
                    }
                }
            }
        }
        if obj.is_null() {
            obj = self.make_str(k)?;
        }
        let f = self.frames.last_mut().unwrap();
        debug_assert!(!f.dict.is_null());
        if !f.key.is_null() {
            unsafe { ffi::Py_DECREF(f.key) };
        }
        f.key = obj;
        f.raw_key = k;
        Ok(())
    }

    #[inline(always)]
    fn string(&mut self, s: &'a [u8]) -> R {
        let o = self.make_str(s)?;
        self.push(o)
    }

    #[inline(always)]
    fn int(&mut self, v: i64) -> R {
        self.push(unsafe { ffi::PyLong_FromLongLong(v) })
    }

    #[inline(always)]
    fn float(&mut self, v: f64) -> R {
        self.push(unsafe { ffi::PyFloat_FromDouble(v) })
    }

    fn color(&mut self, space: &'a [u8], c: [f64; 3]) -> R {
        let s = self.make_str(space)?;
        unsafe {
            let t = ffi::PyTuple_New(4);
            if t.is_null() {
                ffi::Py_DECREF(s);
                return Err(PY_ERR);
            }
            ffi::PyTuple_SET_ITEM(t, 0, s);
            for (i, v) in c.iter().enumerate() {
                let fl = ffi::PyFloat_FromDouble(*v);
                if fl.is_null() {
                    ffi::Py_DECREF(t);
                    return Err(PY_ERR);
                }
                ffi::PyTuple_SET_ITEM(t, i as ffi::Py_ssize_t + 1, fl);
            }
            self.push(t)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_file;

    fn new(input: &str) -> Result<Value<'_>, ParseError> {
        parse_to_value(input, &Filter::All)
    }

    /// Structural equality where floats are compared bitwise (NaN equals NaN).
    fn same(a: &Value, b: &Value) -> bool {
        match (a, b) {
            (Value::Float(x), Value::Float(y)) => x.to_bits() == y.to_bits(),
            (Value::List(x), Value::List(y)) => {
                x.len() == y.len() && x.iter().zip(y).all(|(p, q)| same(p, q))
            }
            (Value::Map(x), Value::Map(y)) => {
                x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).map_or(false, |w| same(v, w)))
            }
            (Value::Color(x), Value::Color(y)) => {
                x.0 == y.0 && [x.1, x.2, x.3].map(f64::to_bits) == [y.1, y.2, y.3].map(f64::to_bits)
            }
            _ => a == b,
        }
    }

    fn same_opt(a: &Option<Value>, b: &Option<Value>) -> bool {
        match (a, b) {
            (Some(a), Some(b)) => same(a, b),
            (None, None) => true,
            _ => false,
        }
    }

    /// Inputs taken from the nom parser's tests plus edge cases; both parsers must agree.
    const CASES: &[&str] = &[
        "a=1",
        "a=-1 b=007 c=+5 d=1.0 e=-1.0 f=1e5 g=.5 h=5. i=18446744073709551615",
        "a=2200.04.03 b=1.1.1 c=.. d=1..2 e=-1.2.3",
        "a=nan b=inf c=infinity d=influence e=nano_shipyard f=NaN g=INF h=+inf",
        "a=\"word\" b=\"Unicode ßäöü😂\" c=\"\\\"Escaped\\\"\" d=\"\" e=\"GATE \\\\\"",
        "a=traits b=target:debris_field_01 c=yes d=ßäöü e=a\u{a0}b",
        "a={} b={ } c={1} d={1 2\t3\n4} e={1 \"text\" 3} f={1 {\"inner\"} 3}",
        "a={a.1=2} b={2=2243.01.03 9=2243.01.10 12=2243.01.13} c={x=1\ny=73.0}",
        "key1=value1\n key2={ list of values }\n key3={ {} {1 2 3} }",
        "a={x=1 y={x=1 y=73.0 z=\"asdf\"\na={\"Anniversary Portraits\"\n \t\"Apocalypse\"}}}",
        "a={intel_manager={ intel={ { 13 { intel=0 stale_intel={} } } { 62 {intel=0 stale_intel={}}} }}}",
        "a={x=1 x=1 y=1 y=2 z=1 z=\"asdf\"}",
        "a={x={1 1 1} x={2 2 2} x={3 3 3}}",
        "a={x={1} x=2 x={3} x={4}} b={x=1 x={2} x={3}} c={x={a=1} x={b=2} x={c=3}}",
        "a={x={} x={} x={}} b={x=rgb {1 2 3} x=rgb {1 2 3}}",
        "1=nano_shipyard",
        "x={ { influence 50 } { alloys 250 } }",
        "key=subject_holdings_limit value=",
        "valueless_key= valued_key=value",
        "expired=yes\n event_id=\t\t\tscope={\n type=none\n id=0\n random={ 0 3991148998 }\n }",
        "a={ b= } c={ b= c=1 } d={ b=1 c= } e={ b= c= d= }",
        "a= \"b\"=1 c= 5=2 d= 1.2.3=4",
        "color = rgb { 1 2 3 }",
        "a=rgb {1 2 3} b=hsv{ 0.5 .5 1e2 } c=rgb {1 2} d={ rgb { 1 2 3 } hsv x } e=rgb f=rgbx",
        "a={ rgb=1 } b={ rgb {1 2 3} } c={ rgb }",
        "\"quoted key\"=1 \"1\"=2 \"\"=3",
        "a = 1 b\t=\n2 c= 3",
        " \n a=1 \n ",
        "a={1=2 01=3 +1=4}",
        // rejected inputs
        "",
        "   ",
        "a",
        "a=1 b",
        "a=b=c",
        "a=1 }",
        "a={",
        "a={1 2",
        "a={ a b=c }",
        "a={ a=1 b }",
        "a={1}{2}",
        "a={ {1}{2} }",
        "a=\"x\"b=\"y\"",
        "a=\"unterminated",
        "a=\"bad \\n escape\"",
        "a=1.2.3x",
        "a=1.2.3.4",
        "a={ 1.2.3x=5 }",
        "a={ 1.2.3x }",
        "a=1e",
        "a=1e_x",
        "a={ 1e=5 }",
        "a= 1e=5",
        "a={ b={1}=2 }",
        "a==",
        "a=<",
        "a=1\u{c}b=2",
        "a=x\u{a0}y=2",
        "=1",
        "a={ =1 }",
        "a=1 #comment",
    ];

    #[test]
    fn test_matches_nom_parser() {
        let mut accepted = 0;
        for case in CASES {
            let old = parse_file(case).ok();
            let new = new(case).ok();
            assert!(same_opt(&old, &new), "input: {:?}\n old: {:?}\n new: {:?}", case, old, new);
            if old.is_some() {
                accepted += 1;
            }
        }
        assert!(accepted > 25);
    }

    /// Differential fuzzing against the nom parser: random token soups, biased towards
    /// well-formed input. Both parsers must accept the same inputs with the same result.
    /// `FUZZ_ITERS` overrides the number of inputs.
    #[test]
    fn test_fuzz_against_nom_parser() {
        const TOKENS: &[&str] = &[
            "{", "}", "=", " ", " ", " ", "\n", "\t", "{ ", " }", "= ", "a", "b", "key", "rgb", "hsv",
            "rgb {", "1", "2", "-1", "+3", "007", "1.5", ".5", "5.", "1.2.3", "2200.01.01", "1e", "1e5",
            "e", "nan", "inf", "infinity", "NaN", "\"s\"", "\"\"", "\"a\\\"b\"", "\"x y\"", "\"{\"",
            "\"}\"", "\"=\"", "\\", "\"", "x", ".", "+", "-", "\u{a0}", "\u{c}", "ß", "#", "<", "yes",
            "a=1", "b={", "a= ", "={", "}=", "99999999999999999999", "1 2 3", "{}", "{ }",
        ];
        let iters: u64 =
            std::env::var("FUZZ_ITERS").ok().and_then(|v| v.parse().ok()).unwrap_or(150_000);
        let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
        let mut next = move || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state
        };
        let (mut accepted, mut rejected) = (0u64, 0u64);
        for _ in 0..iters {
            let n = 1 + next() % 14;
            let mut input = String::new();
            // start most inputs with a key so that a good share is accepted
            if next() % 4 != 0 {
                input.push_str("k=");
            }
            let mut open = 0;
            for _ in 0..n {
                let t = TOKENS[(next() % TOKENS.len() as u64) as usize];
                open += t.matches('{').count() as i64 - t.matches('}').count() as i64;
                input.push_str(t);
                if next() % 3 != 0 {
                    input.push(' ');
                }
            }
            if next() % 2 == 0 {
                for _ in 0..open.max(0) {
                    input.push_str(" }");
                }
            }
            let old = parse_file(&input).ok();
            let new = new(&input).ok();
            assert!(same_opt(&old, &new), "input: {:?}\n old: {:?}\n new: {:?}", input, old, new);
            if old.is_none() {
                rejected += 1;
                continue;
            }
            accepted += 1;
            // the event tape must not change the result
            let mut tape = TapeSink::new(input.as_bytes());
            parse_document(input.as_bytes(), &Filter::All, &mut tape).unwrap();
            let mut sink = ValueSink::default();
            replay(input.as_bytes(), &tape.events, &mut sink).unwrap();
            assert!(same_opt(&old, &sink.result.take()), "tape, input: {:?}", input);
            // a filter that keeps everything must not change the result
            let keep =
                Filter::Sel { keys: HashMap::default(), default: Some(Box::new(Filter::All)) };
            let kept = parse_to_value(&input, &keep).ok();
            assert!(same_opt(&old, &kept), "keep-all, input: {:?}", input);
            // projection == the full result restricted to the selected top-level keys
            let selected = ["k", "b", "key"];
            let Ok(Value::Map(projected)) = parse_to_value(&input, &sel(&selected)) else {
                panic!("projection rejected an accepted input: {:?}", input);
            };
            let Some(Value::Map(full)) = &old else { panic!() };
            for (k, v) in &projected {
                assert!(full.get(k).map_or(false, |w| same(v, w)), "projection: {:?}", input);
            }
            for k in selected {
                assert_eq!(full.contains_key(k), projected.contains_key(k), "projection: {:?}", input);
            }
            assert!(projected.len() <= selected.len());
        }
        println!("fuzz: {} accepted, {} rejected", accepted, rejected);
        assert!(accepted > iters / 50);
    }

    #[test]
    fn test_tape_replay_matches() {
        for case in CASES {
            let direct = new(case).ok();
            let mut tape = TapeSink::new(case.as_bytes());
            let via_tape = match parse_document(case.as_bytes(), &Filter::All, &mut tape) {
                Ok(()) => {
                    let mut sink = ValueSink::default();
                    replay(case.as_bytes(), &tape.events, &mut sink).unwrap();
                    sink.result.take()
                }
                Err(_) => None,
            };
            assert!(same_opt(&direct, &via_tape), "input: {:?}", case);
        }
    }

    #[test]
    fn test_deep_nesting() {
        let depth = 250;
        let input = format!("outer_key={}{}{}", "{".repeat(depth), "key=value", "}".repeat(depth));
        assert_eq!(new(&input).ok(), parse_file(&input).ok());
        let too_deep = format!("k={}{}", "{".repeat(5000), "}".repeat(5000));
        assert!(new(&too_deep).is_err());
    }

    fn sel(names: &[&str]) -> Filter {
        let mut keys: HashMap<Vec<u8>, Filter, Fx> = HashMap::default();
        for n in names {
            keys.insert(n.as_bytes().to_vec(), Filter::All);
        }
        Filter::Sel { keys, default: None }
    }

    #[test]
    fn test_projection() {
        let input = "a=1 skip={ x=\"}\" y={ { } } z=\"\\\"}\" } b={ c=2 } skip2=rgb { 1 2 3 } \
                     skip3= skip4=5 skip5=\"s\" c=3 skip6= d= e=4 a=2";
        let full = new(input).unwrap();
        let projected = parse_to_value(input, &sel(&["a", "b", "c", "d", "e"])).unwrap();
        let (Value::Map(full), Value::Map(projected)) = (full, projected) else { panic!() };
        assert_eq!(projected.len(), 4);
        for (k, v) in &projected {
            assert_eq!(full.get(k), Some(v));
        }
        assert_eq!(projected.get("a"), Some(&Value::List(vec![Value::Int(1), Value::Int(2)])));

        // nested: keep only `name` of every entry of `country`
        let input = "country={ 0={ name=\"A\" big={ 1 2 3 } } 1={ big={ } name=\"B\" } } other=1";
        let mut inner: HashMap<Vec<u8>, Filter, Fx> = HashMap::default();
        inner.insert(b"name".to_vec(), Filter::All);
        let per_country = Filter::Sel { keys: inner, default: None };
        let countries = Filter::Sel { keys: HashMap::default(), default: Some(Box::new(per_country)) };
        let mut top: HashMap<Vec<u8>, Filter, Fx> = HashMap::default();
        top.insert(b"country".to_vec(), countries);
        let projected = parse_to_value(input, &Filter::Sel { keys: top, default: None }).unwrap();
        let expected = new("country={ 0={ name=\"A\" } 1={ name=\"B\" } }").unwrap();
        assert_eq!(projected, expected);
    }
}
