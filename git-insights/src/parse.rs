//! Streaming parser for `git log -z --numstat` output in the format produced by [`crate::git::LOG_FORMAT`].
//!
//! With `-z` every record and every numstat entry is NUL-terminated, so file names may contain spaces,
//! quotes, tabs or even newlines without any escaping:
//!
//! ```text
//! \x1e<hash>\x1f<name>\x1f<email>\x1f<unix time>\x1f<subject>\0     commit header
//! \n<added>\t<deleted>\t<path>\0                                      first entry starts with '\n'
//! <added>\t<deleted>\t\0<old path>\0<new path>\0                      rename / copy
//! -\t-\t<path>\0                                                      binary file
//! ```
//!
//! A commit without file changes (e.g. `--allow-empty`) has no entries and no leading '\n'.

use std::io::BufRead;

use anyhow::{Context, Result, bail};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Change {
    pub path: String,
    /// Set when git detected a rename (or copy) from this path.
    pub old_path: Option<String>,
    /// `None` for binary files.
    pub added: Option<u32>,
    pub deleted: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Commit {
    pub hash: String,
    pub author_name: String,
    pub author_email: String,
    pub time: i64,
    pub subject: String,
    pub changes: Vec<Change>,
}

/// Iterator over commits read from a `git log` byte stream.
pub struct LogParser<R: BufRead> {
    reader: R,
    buf: Vec<u8>,
    pending: Option<Commit>,
    done: bool,
}

impl<R: BufRead> LogParser<R> {
    pub fn new(reader: R) -> Self {
        Self {
            reader,
            buf: Vec::with_capacity(256),
            pending: None,
            done: false,
        }
    }

    /// Next NUL-terminated token (without the terminator), or `None` at end of input.
    fn token(&mut self) -> Result<Option<String>> {
        self.buf.clear();
        let n = self
            .reader
            .read_until(0, &mut self.buf)
            .context("reading git log output")?;
        if n == 0 {
            return Ok(None);
        }
        if self.buf.last() == Some(&0) {
            self.buf.pop();
        }
        Ok(Some(String::from_utf8_lossy(&self.buf).into_owned()))
    }

    fn step(&mut self) -> Result<Option<Commit>> {
        loop {
            let Some(token) = self.token()? else {
                self.done = true;
                return Ok(self.pending.take());
            };
            if let Some(header) = token.strip_prefix('\x1e') {
                let next = parse_header(header)?;
                if let Some(prev) = self.pending.replace(next) {
                    return Ok(Some(prev));
                }
                continue;
            }
            let entry = token.strip_prefix('\n').unwrap_or(&token);
            if entry.is_empty() {
                continue; // trailing newline at the very end of the stream
            }
            let Some(commit) = self.pending.as_mut() else {
                bail!("numstat entry before any commit header: {entry:?}");
            };
            let mut parts = entry.splitn(3, '\t');
            let (Some(added), Some(deleted), Some(path)) =
                (parts.next(), parts.next(), parts.next())
            else {
                bail!(
                    "malformed numstat entry in commit {}: {entry:?}",
                    commit.hash
                );
            };
            let added = parse_count(added)?;
            let deleted = parse_count(deleted)?;
            let change = if path.is_empty() {
                // Rename: the two paths follow as separate tokens.
                let old = self
                    .token()?
                    .context("truncated rename entry (missing old path)")?;
                let new = self
                    .token()?
                    .context("truncated rename entry (missing new path)")?;
                Change {
                    path: new,
                    old_path: Some(old),
                    added,
                    deleted,
                }
            } else {
                Change {
                    path: path.to_string(),
                    old_path: None,
                    added,
                    deleted,
                }
            };
            // Re-borrow: self.token() above needed &mut self.
            self.pending
                .as_mut()
                .expect("checked above")
                .changes
                .push(change);
        }
    }
}

impl<R: BufRead> Iterator for LogParser<R> {
    type Item = Result<Commit>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        match self.step() {
            Ok(Some(c)) => Some(Ok(c)),
            Ok(None) => None,
            Err(e) => {
                self.done = true;
                Some(Err(e))
            }
        }
    }
}

fn parse_count(s: &str) -> Result<Option<u32>> {
    if s == "-" {
        return Ok(None);
    }
    s.parse::<u32>()
        .map(Some)
        .with_context(|| format!("invalid line count {s:?}"))
}

fn parse_header(h: &str) -> Result<Commit> {
    // The subject is last and splitn keeps any separator characters it may contain.
    let mut f = h.splitn(5, '\x1f');
    let (Some(hash), Some(name), Some(email), Some(time), subject) =
        (f.next(), f.next(), f.next(), f.next(), f.next())
    else {
        bail!("malformed commit header {h:?}");
    };
    Ok(Commit {
        hash: hash.to_string(),
        author_name: name.to_string(),
        author_email: email.to_string(),
        time: time
            .trim()
            .parse()
            .with_context(|| format!("invalid commit time {time:?}"))?,
        subject: subject.unwrap_or_default().to_string(),
        changes: Vec::new(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(bytes: &[u8]) -> Vec<Commit> {
        LogParser::new(bytes).collect::<Result<Vec<_>>>().unwrap()
    }

    #[test]
    fn parses_real_git_output_with_rename_binary_and_odd_names() {
        // Captured from `git log -z --numstat -M` on a scratch repository (hashes shortened).
        let raw = b"\x1eaaa\x1f\xd0\x9e\xd0\xbb\xd1\x8f\x1fo@x.ua\x1f1793798488\x1fempty commit\0\
\x1ebbb\x1f\xd0\x9e\xd0\xbb\xd1\x8f\x1fo@x.ua\x1f1793798486\x1fmv\0\n1\t0\t\0big.txt\0dir/renamed big.txt\0\
\x1eccc\x1f\xd0\x9e\xd0\xbb\xd1\x8f\x1fo@x.ua\x1f1793798479\x1finit: first\0\n-\t-\tbin.dat\0\
2\t0\tfile one.txt\0\
1\t0\tnew\nline.txt\0";
        let commits = parse(raw);
        assert_eq!(commits.len(), 3);

        assert_eq!(commits[0].hash, "aaa");
        assert_eq!(commits[0].author_name, "Оля");
        assert!(commits[0].changes.is_empty(), "empty commit has no changes");

        assert_eq!(
            commits[1].changes,
            vec![Change {
                path: "dir/renamed big.txt".into(),
                old_path: Some("big.txt".into()),
                added: Some(1),
                deleted: Some(0)
            }]
        );

        let c = &commits[2];
        assert_eq!(c.time, 1_793_798_479);
        assert_eq!(c.subject, "init: first");
        assert_eq!(
            c.changes[0],
            Change {
                path: "bin.dat".into(),
                old_path: None,
                added: None,
                deleted: None
            }
        );
        assert_eq!(c.changes[1].path, "file one.txt");
        assert_eq!(
            c.changes[2].path, "new\nline.txt",
            "newline inside a file name survives"
        );
    }

    #[test]
    fn subject_may_contain_separators() {
        let commits = parse(b"\x1eh\x1fA\x1fa@x\x1f10\x1ffix: a\x1fb | c\0\n3\t1\tsrc/x.rs\0");
        assert_eq!(commits[0].subject, "fix: a\x1fb | c");
        assert_eq!(commits[0].changes[0].added, Some(3));
        assert_eq!(commits[0].changes[0].deleted, Some(1));
    }

    #[test]
    fn empty_input_yields_nothing() {
        assert!(parse(b"").is_empty());
    }

    #[test]
    fn malformed_input_is_an_error_not_a_panic() {
        for bad in [
            &b"\n1\t2\tx\0"[..],
            b"\x1eh\x1fA\0",
            b"\x1eh\x1fA\x1fa\x1fnot-a-number\x1fs\0",
            b"\x1eh\x1fA\x1fa\x1f1\x1fs\0\nx\ty\tz\0",
        ] {
            let result: Result<Vec<_>> = LogParser::new(bad).collect();
            assert!(result.is_err(), "{bad:?} should fail");
        }
        let truncated: Result<Vec<_>> =
            LogParser::new(&b"\x1eh\x1fA\x1fa\x1f1\x1fs\0\n1\t0\t\0old\0"[..]).collect();
        assert!(truncated.is_err());
    }
}
