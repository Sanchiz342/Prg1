//! Terminal tables, CSV and small formatting helpers (no heavyweight table/colour/date dependencies).

use std::io::IsTerminal;

use unicode_width::UnicodeWidthStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Right,
}

pub struct Style {
    pub color: bool,
}

impl Style {
    /// Colour only on a terminal, and never when `NO_COLOR` is set (https://no-color.org).
    pub fn detect(no_color: bool) -> Self {
        let color =
            !no_color && std::env::var_os("NO_COLOR").is_none() && std::io::stdout().is_terminal();
        Self { color }
    }
    fn paint(&self, code: &str, s: &str) -> String {
        if self.color {
            format!("\x1b[{code}m{s}\x1b[0m")
        } else {
            s.to_string()
        }
    }
    pub fn bold(&self, s: &str) -> String {
        self.paint("1", s)
    }
    pub fn dim(&self, s: &str) -> String {
        self.paint("2", s)
    }
    pub fn red(&self, s: &str) -> String {
        self.paint("31", s)
    }
    pub fn yellow(&self, s: &str) -> String {
        self.paint("33", s)
    }
    pub fn green(&self, s: &str) -> String {
        self.paint("32", s)
    }
}

/// Visible width, ignoring ANSI colour sequences.
fn width(s: &str) -> usize {
    let mut plain = String::with_capacity(s.len());
    let mut in_escape = false;
    for c in s.chars() {
        match (in_escape, c) {
            (false, '\x1b') => in_escape = true,
            (true, 'm') => in_escape = false,
            (true, _) => {}
            (false, c) => plain.push(c),
        }
    }
    UnicodeWidthStr::width(plain.as_str())
}

/// Shortens from the left so the end of a path (the file name) stays visible: "…/deep/file.rs".
pub fn truncate_left(s: &str, max: usize) -> String {
    if UnicodeWidthStr::width(s) <= max || max < 2 {
        return s.to_string();
    }
    let mut out: Vec<char> = Vec::new();
    let mut w = 1; // the ellipsis
    for c in s.chars().rev() {
        let cw = unicode_width::UnicodeWidthChar::width(c).unwrap_or(0);
        if w + cw > max {
            break;
        }
        w += cw;
        out.push(c);
    }
    out.reverse();
    format!("…{}", out.into_iter().collect::<String>())
}

/// Renders a table; the column marked `flex` is shrunk (from the left) to fit `max_width`.
pub fn table(
    headers: &[&str],
    aligns: &[Align],
    rows: &[Vec<String>],
    flex: Option<usize>,
    max_width: usize,
    st: &Style,
) -> String {
    let n = headers.len();
    let mut widths: Vec<usize> = headers.iter().map(|h| width(h)).collect();
    for r in rows {
        for (i, c) in r.iter().enumerate() {
            widths[i] = widths[i].max(width(c));
        }
    }
    let total: usize = widths.iter().sum::<usize>() + 2 * (n - 1);
    let mut rows = rows.to_vec();
    if let Some(f) = flex
        && total > max_width
    {
        let others = total - widths[f];
        let target = max_width.saturating_sub(others).max(12);
        if target < widths[f] {
            widths[f] = target;
            for r in &mut rows {
                r[f] = truncate_left(&r[f], target);
            }
        }
    }
    let line = |cells: &[String]| -> String {
        let mut s = String::new();
        for (i, c) in cells.iter().enumerate() {
            let pad = widths[i].saturating_sub(width(c));
            if aligns[i] == Align::Right {
                s.push_str(&" ".repeat(pad));
                s.push_str(c);
            } else {
                s.push_str(c);
                if i + 1 < n {
                    s.push_str(&" ".repeat(pad));
                }
            }
            if i + 1 < n {
                s.push_str("  ");
            }
        }
        s.trim_end().to_string()
    };
    let head: Vec<String> = headers.iter().map(|h| st.bold(h)).collect();
    let mut out = line(&head);
    out.push('\n');
    out.push_str(&st.dim(&"─".repeat(widths.iter().sum::<usize>() + 2 * (n - 1))));
    out.push('\n');
    for r in &rows {
        out.push_str(&line(r));
        out.push('\n');
    }
    out
}

pub fn terminal_width() -> usize {
    std::env::var("COLUMNS")
        .ok()
        .and_then(|c| c.parse().ok())
        .filter(|&w| w >= 40)
        .unwrap_or(120)
}

/// RFC 4180 CSV.
pub fn csv(headers: &[&str], rows: &[Vec<String>]) -> String {
    let esc = |s: &str| {
        if s.contains([',', '"', '\n', '\r']) {
            format!("\"{}\"", s.replace('"', "\"\""))
        } else {
            s.to_string()
        }
    };
    let mut out = headers.iter().map(|h| esc(h)).collect::<Vec<_>>().join(",");
    out.push('\n');
    for r in rows {
        out.push_str(&r.iter().map(|c| esc(c)).collect::<Vec<_>>().join(","));
        out.push('\n');
    }
    out
}

/// Unix seconds -> "YYYY-MM-DD" (UTC), using Howard Hinnant's civil-from-days algorithm.
pub fn date(ts: i64) -> String {
    let days = ts.div_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

/// 1234567 -> "1,234,567"
pub fn num(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// "3 days", "5 months", "2 years" between two timestamps.
pub fn span(seconds: i64) -> String {
    let days = seconds.max(0) / 86_400;
    match days {
        0 => "less than a day".into(),
        1 => "1 day".into(),
        2..=59 => format!("{days} days"),
        60..=729 => format!("{} months", days / 30),
        _ => format!("{} years", days / 365),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates() {
        assert_eq!(date(0), "1970-01-01");
        assert_eq!(date(951_782_400), "2000-02-29"); // leap day
        assert_eq!(date(1_793_798_479), "2026-11-04");
        assert_eq!(date(-86_400), "1969-12-31");
    }

    #[test]
    fn numbers_and_spans() {
        assert_eq!(num(0), "0");
        assert_eq!(num(1_234_567), "1,234,567");
        assert_eq!(span(3 * 86_400), "3 days");
        assert_eq!(span(400 * 86_400), "13 months");
        assert_eq!(span(3 * 365 * 86_400), "3 years");
    }

    #[test]
    fn truncation_keeps_the_file_name() {
        assert_eq!(
            truncate_left("src/very/deep/module/file.rs", 12),
            "…ule/file.rs"
        );
        assert_eq!(truncate_left("short.rs", 12), "short.rs");
    }

    #[test]
    fn table_aligns_wide_characters_and_shrinks_flex_column() {
        let st = Style { color: false };
        let rows = vec![
            vec!["Оля".to_string(), "12".to_string()],
            vec!["Bob".to_string(), "3".to_string()],
        ];
        let t = table(
            &["name", "n"],
            &[Align::Left, Align::Right],
            &rows,
            None,
            80,
            &st,
        );
        let lines: Vec<&str> = t.lines().collect();
        assert_eq!(lines[2], "Оля   12");
        assert_eq!(lines[3], "Bob    3");

        let long = vec![vec![
            "a/very/long/path/to/some/file.rs".to_string(),
            "1".to_string(),
        ]];
        let t = table(
            &["path", "n"],
            &[Align::Left, Align::Right],
            &long,
            Some(0),
            20,
            &st,
        );
        assert!(t.lines().nth(2).unwrap().starts_with('…'));
    }

    #[test]
    fn csv_escaping() {
        let out = csv(&["a", "b"], &[vec!["x,y".into(), "say \"hi\"".into()]]);
        assert_eq!(out, "a,b\n\"x,y\",\"say \"\"hi\"\"\"\n");
    }
}
