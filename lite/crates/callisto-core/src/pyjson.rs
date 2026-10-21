//! JSON as Python's `json` module reads and writes it: `NaN`, `Infinity`
//! and `-Infinity` are accepted and written, floats are written as `repr`
//! writes them, and [`dumps`] lays a document out as
//! `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)` does.
//! The full app's project, preset and recovery files are written this way.

use std::collections::BTreeMap;
use std::fmt::Write as _;

/// A JSON value; objects keep their keys sorted (`sort_keys=True`).
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    Object(BTreeMap<String, Json>),
}

pub type Object = BTreeMap<String, Json>;

impl Json {
    pub fn object() -> Json {
        Json::Object(BTreeMap::new())
    }

    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Object(m) => m.get(key),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&Object> {
        match self {
            Json::Object(m) => Some(m),
            _ => None,
        }
    }

    pub fn as_object_mut(&mut self) -> Option<&mut Object> {
        match self {
            Json::Object(m) => Some(m),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&Vec<Json>> {
        match self {
            Json::Array(v) => Some(v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Json::Null)
    }

    /// `float(value)` for numbers and booleans; `float(str)` for numeric text.
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Json::Int(i) => Some(*i as f64),
            Json::Float(f) => Some(*f),
            Json::Bool(b) => Some(f64::from(u8::from(*b))),
            Json::Str(s) => py_float(s),
            _ => None,
        }
    }

    /// `int(value)`: floats truncate, numeric text is parsed.
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Json::Int(i) => Some(*i),
            Json::Float(f) if f.is_finite() => Some(f.trunc() as i64),
            Json::Bool(b) => Some(i64::from(*b)),
            Json::Str(s) => s.trim().parse().ok(),
            _ => None,
        }
    }

    /// `bool(value)`, Python's truthiness.
    pub fn truthy(&self) -> bool {
        match self {
            Json::Null => false,
            Json::Bool(b) => *b,
            Json::Int(i) => *i != 0,
            Json::Float(f) => *f != 0.0,
            Json::Str(s) => !s.is_empty(),
            Json::Array(v) => !v.is_empty(),
            Json::Object(m) => !m.is_empty(),
        }
    }

    /// `str(value)` for the scalars Python would print.
    pub fn py_str(&self) -> String {
        match self {
            Json::Null => "None".into(),
            Json::Bool(b) => if *b { "True" } else { "False" }.into(),
            Json::Int(i) => i.to_string(),
            Json::Float(f) => float_repr(*f),
            Json::Str(s) => s.clone(),
            other => dumps_compact(other),
        }
    }
}

impl From<bool> for Json {
    fn from(v: bool) -> Self {
        Json::Bool(v)
    }
}

impl From<i64> for Json {
    fn from(v: i64) -> Self {
        Json::Int(v)
    }
}

impl From<usize> for Json {
    fn from(v: usize) -> Self {
        Json::Int(v as i64)
    }
}

impl From<f64> for Json {
    fn from(v: f64) -> Self {
        Json::Float(v)
    }
}

impl From<&str> for Json {
    fn from(v: &str) -> Self {
        Json::Str(v.to_owned())
    }
}

impl From<String> for Json {
    fn from(v: String) -> Self {
        Json::Str(v)
    }
}

impl<T: Into<Json>> From<Option<T>> for Json {
    fn from(v: Option<T>) -> Self {
        v.map_or(Json::Null, Into::into)
    }
}

impl<T: Into<Json>> From<Vec<T>> for Json {
    fn from(v: Vec<T>) -> Self {
        Json::Array(v.into_iter().map(Into::into).collect())
    }
}

/// `float(text)`: Python's spelling of numbers, `nan` and `inf` included.
pub fn py_float(text: &str) -> Option<f64> {
    let t = text.trim().replace('_', "");
    match t.to_ascii_lowercase().as_str() {
        "nan" | "+nan" | "-nan" => Some(f64::NAN),
        "inf" | "+inf" | "infinity" | "+infinity" => Some(f64::INFINITY),
        "-inf" | "-infinity" => Some(f64::NEG_INFINITY),
        _ => t.parse().ok(),
    }
}

/// `repr(float)`: the shortest digits that read back, fixed notation for
/// decimal exponents from -4 to 15, `1e+16` style otherwise.
pub fn float_repr(v: f64) -> String {
    if v.is_nan() {
        return "nan".into();
    }
    if v.is_infinite() {
        return if v > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if v == 0.0 {
        return if v.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    let sign = if v < 0.0 { "-" } else { "" };
    // Rust's `{:e}` gives the shortest round-trip digits: D.DDDDe±N.
    let e = format!("{:e}", v.abs());
    let (mantissa, exp) = e.split_once('e').unwrap_or((&e, "0"));
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let decpt = exp.parse::<i32>().unwrap_or(0) + 1;
    let n = digits.len() as i32;
    let body = if decpt <= -4 || decpt > 16 {
        let (first, rest) = digits.split_at(1);
        let x = decpt - 1;
        let frac = if rest.is_empty() { String::new() } else { format!(".{rest}") };
        format!("{first}{frac}e{}{:02}", if x < 0 { '-' } else { '+' }, x.abs())
    } else if decpt <= 0 {
        format!("0.{}{digits}", "0".repeat((-decpt) as usize))
    } else if decpt >= n {
        format!("{digits}{}.0", "0".repeat((decpt - n) as usize))
    } else {
        let (a, b) = digits.split_at(decpt as usize);
        format!("{a}.{b}")
    };
    format!("{sign}{body}")
}

/// A float as `json.dumps` writes it.
fn json_float(v: f64) -> String {
    if v.is_nan() {
        "NaN".into()
    } else if v.is_infinite() {
        if v > 0.0 { "Infinity".into() } else { "-Infinity".into() }
    } else {
        float_repr(v)
    }
}

/// `json.encoder.py_encode_basestring` (`ensure_ascii=False`).
fn write_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_value(out: &mut String, v: &Json, indent: Option<usize>, level: usize) {
    match v {
        Json::Null => out.push_str("null"),
        Json::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Json::Int(i) => {
            let _ = write!(out, "{i}");
        }
        Json::Float(f) => out.push_str(&json_float(*f)),
        Json::Str(s) => write_str(out, s),
        Json::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if indent.is_none() {
                        out.push(' ');
                    }
                }
                newline(out, indent, level + 1);
                write_value(out, item, indent, level + 1);
            }
            newline(out, indent, level);
            out.push(']');
        }
        Json::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            for (i, (k, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if indent.is_none() {
                        out.push(' ');
                    }
                }
                newline(out, indent, level + 1);
                write_str(out, k);
                out.push_str(": ");
                write_value(out, item, indent, level + 1);
            }
            newline(out, indent, level);
            out.push('}');
        }
    }
}

fn newline(out: &mut String, indent: Option<usize>, level: usize) {
    if let Some(n) = indent {
        out.push('\n');
        out.push_str(&" ".repeat(n * level));
    }
}

/// `json.dumps(v, indent=2, sort_keys=True, ensure_ascii=False)`.
pub fn dumps(v: &Json) -> String {
    let mut out = String::new();
    write_value(&mut out, v, Some(2), 0);
    out
}

/// `json.dumps(v, sort_keys=True, ensure_ascii=False)`: one line, `", "` and `": "`.
pub fn dumps_compact(v: &Json) -> String {
    let mut out = String::new();
    write_value(&mut out, v, None, 0);
    out
}

/// `json.loads`, with Python's `NaN`, `Infinity` and `-Infinity`.
pub fn loads(text: &str) -> Result<Json, String> {
    let mut p = Parser { s: text.as_bytes(), i: 0 };
    p.ws();
    let v = p.value()?;
    p.ws();
    if p.i != p.s.len() {
        return Err(format!("Extra data at {}", p.i));
    }
    Ok(v)
}

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
}

impl Parser<'_> {
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn eat(&mut self, lit: &str) -> bool {
        if self.s[self.i..].starts_with(lit.as_bytes()) {
            self.i += lit.len();
            true
        } else {
            false
        }
    }

    fn err<T>(&self, what: &str) -> Result<T, String> {
        Err(format!("{what} at {}", self.i))
    }

    fn value(&mut self) -> Result<Json, String> {
        let Some(&c) = self.s.get(self.i) else { return self.err("Expecting value") };
        match c {
            b'{' => self.object(),
            b'[' => self.array(),
            b'"' => Ok(Json::Str(self.string()?)),
            b't' if self.eat("true") => Ok(Json::Bool(true)),
            b'f' if self.eat("false") => Ok(Json::Bool(false)),
            b'n' if self.eat("null") => Ok(Json::Null),
            b'N' if self.eat("NaN") => Ok(Json::Float(f64::NAN)),
            b'I' if self.eat("Infinity") => Ok(Json::Float(f64::INFINITY)),
            b'-' if self.eat("-Infinity") => Ok(Json::Float(f64::NEG_INFINITY)),
            b'-' | b'0'..=b'9' => self.number(),
            _ => self.err("Expecting value"),
        }
    }

    fn number(&mut self) -> Result<Json, String> {
        let start = self.i;
        let mut float = false;
        while let Some(&c) = self.s.get(self.i) {
            match c {
                b'0'..=b'9' | b'-' | b'+' => {}
                b'.' | b'e' | b'E' => float = true,
                _ => break,
            }
            self.i += 1;
        }
        let text = std::str::from_utf8(&self.s[start..self.i]).unwrap_or_default();
        if !float && let Ok(i) = text.parse::<i64>() {
            return Ok(Json::Int(i));
        }
        text.parse::<f64>().map(Json::Float).or_else(|_| self.err("Invalid number"))
    }

    fn hex4(&mut self) -> Result<u32, String> {
        let h = self
            .s
            .get(self.i..self.i + 4)
            .and_then(|b| std::str::from_utf8(b).ok())
            .and_then(|t| u32::from_str_radix(t, 16).ok());
        let Some(h) = h else { return self.err("Invalid \\uXXXX escape") };
        self.i += 4;
        Ok(h)
    }

    fn string(&mut self) -> Result<String, String> {
        self.i += 1;
        let mut out = String::new();
        loop {
            let Some(&c) = self.s.get(self.i) else { return self.err("Unterminated string") };
            match c {
                b'"' => {
                    self.i += 1;
                    return Ok(out);
                }
                b'\\' => {
                    self.i += 1;
                    let Some(&e) = self.s.get(self.i) else { return self.err("Unterminated string") };
                    self.i += 1;
                    match e {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{08}'),
                        b'f' => out.push('\u{0c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let mut code = self.hex4()?;
                            if (0xd800..0xdc00).contains(&code) && self.s[self.i..].starts_with(b"\\u") {
                                let save = self.i;
                                self.i += 2;
                                let low = self.hex4()?;
                                if (0xdc00..0xe000).contains(&low) {
                                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                                } else {
                                    self.i = save;
                                }
                            }
                            out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
                        }
                        _ => return self.err("Invalid \\escape"),
                    }
                }
                _ => {
                    // A whole UTF-8 sequence.
                    let len = match c {
                        0x00..=0x7f => 1,
                        0xc0..=0xdf => 2,
                        0xe0..=0xef => 3,
                        _ => 4,
                    };
                    let end = (self.i + len).min(self.s.len());
                    out.push_str(&String::from_utf8_lossy(&self.s[self.i..end]));
                    self.i = end;
                }
            }
        }
    }

    fn array(&mut self) -> Result<Json, String> {
        self.i += 1;
        let mut items = Vec::new();
        self.ws();
        if self.eat("]") {
            return Ok(Json::Array(items));
        }
        loop {
            self.ws();
            items.push(self.value()?);
            self.ws();
            if self.eat(",") {
                continue;
            }
            if self.eat("]") {
                return Ok(Json::Array(items));
            }
            return self.err("Expecting ',' delimiter");
        }
    }

    fn object(&mut self) -> Result<Json, String> {
        self.i += 1;
        let mut map = BTreeMap::new();
        self.ws();
        if self.eat("}") {
            return Ok(Json::Object(map));
        }
        loop {
            self.ws();
            if self.s.get(self.i) != Some(&b'"') {
                return self.err("Expecting property name enclosed in double quotes");
            }
            let k = self.string()?;
            self.ws();
            if !self.eat(":") {
                return self.err("Expecting ':' delimiter");
            }
            self.ws();
            let v = self.value()?;
            map.insert(k, v);
            self.ws();
            if self.eat(",") {
                continue;
            }
            if self.eat("}") {
                return Ok(Json::Object(map));
            }
            return self.err("Expecting ',' delimiter");
        }
    }
}

/// Converts a `serde_json` value (finite numbers only).
pub fn from_serde(v: &serde_json::Value) -> Json {
    match v {
        serde_json::Value::Null => Json::Null,
        serde_json::Value::Bool(b) => Json::Bool(*b),
        serde_json::Value::Number(n) => {
            n.as_i64().map(Json::Int).unwrap_or_else(|| Json::Float(n.as_f64().unwrap_or(f64::NAN)))
        }
        serde_json::Value::String(s) => Json::Str(s.clone()),
        serde_json::Value::Array(a) => Json::Array(a.iter().map(from_serde).collect()),
        serde_json::Value::Object(m) => Json::Object(m.iter().map(|(k, v)| (k.clone(), from_serde(v))).collect()),
    }
}

/// Converts to a `serde_json` value; NaN and infinities become `null`.
pub fn to_serde(v: &Json) -> serde_json::Value {
    match v {
        Json::Null => serde_json::Value::Null,
        Json::Bool(b) => serde_json::Value::Bool(*b),
        Json::Int(i) => serde_json::Value::from(*i),
        Json::Float(f) => serde_json::Number::from_f64(*f).map_or(serde_json::Value::Null, serde_json::Value::Number),
        Json::Str(s) => serde_json::Value::String(s.clone()),
        Json::Array(a) => serde_json::Value::Array(a.iter().map(to_serde).collect()),
        Json::Object(m) => serde_json::Value::Object(m.iter().map(|(k, v)| (k.clone(), to_serde(v))).collect()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_print_as_python_repr() {
        for (v, want) in [
            (1.0, "1.0"),
            (0.1, "0.1"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (123456789.123, "123456789.123"),
            (-2.5, "-2.5"),
            (1.2345678901234568e17, "1.2345678901234568e+17"),
            (45.0625, "45.0625"),
            (-0.0, "-0.0"),
        ] {
            assert_eq!(float_repr(v), want, "{v:e}");
        }
    }

    #[test]
    fn dumps_like_python() {
        let v =
            loads(r#"{"b": [1, 2.5, NaN, -Infinity], "a": {}, "c": [], "d": {"x": "é\n\u0001"}, "e": null}"#).unwrap();
        // json.dumps(json.loads(...), indent=2, sort_keys=True, ensure_ascii=False)
        let want = "{\n  \"a\": {},\n  \"b\": [\n    1,\n    2.5,\n    NaN,\n    -Infinity\n  ],\n  \"c\": [],\n  \"d\": {\n    \"x\": \"é\\n\\u0001\"\n  },\n  \"e\": null\n}";
        assert_eq!(dumps(&v), want);
        assert_eq!(dumps_compact(&loads("[1, {\"k\": true}]").unwrap()), "[1, {\"k\": true}]");
    }

    #[test]
    fn surrogate_pairs_and_numbers() {
        assert_eq!(loads(r#""😀""#).unwrap(), Json::Str("😀".into()));
        assert_eq!(loads("-12").unwrap(), Json::Int(-12));
        assert_eq!(loads("1e3").unwrap(), Json::Float(1000.0));
        assert!(loads("{\"a\" 1}").is_err());
    }
}
