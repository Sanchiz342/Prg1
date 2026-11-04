//! Thin wrappers around the `git` executable. Using the CLI (instead of a libgit2 binding) keeps the binary
//! small, honours the user's git configuration and `.mailmap`, and supports every repository git supports.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;

use anyhow::{Context, Result, bail};

use crate::parse::LogParser;

/// `%aN` / `%aE` apply `.mailmap`, so one person with several identities can be merged by the project.
pub const LOG_FORMAT: &str = "--format=%x1e%H%x1f%aN%x1f%aE%x1f%at%x1f%s";

#[derive(Debug, Clone, Default)]
pub struct LogOptions {
    pub rev: String,
    pub since: Option<String>,
    pub until: Option<String>,
}

pub struct Repo {
    pub root: PathBuf,
}

impl Repo {
    pub fn open(path: &Path) -> Result<Self> {
        let out = Command::new("git")
            .arg("-C")
            .arg(path)
            .args(["rev-parse", "--show-toplevel"])
            .output()
            .context("failed to run `git` — is it installed and on PATH?")?;
        if out.status.success() {
            let root = String::from_utf8_lossy(&out.stdout).trim().to_string();
            return Ok(Self {
                root: PathBuf::from(root),
            });
        }
        // Bare repositories have no top level; fall back to the git dir itself.
        let bare = Command::new("git")
            .arg("-C")
            .arg(path)
            .args(["rev-parse", "--is-bare-repository"])
            .output()?;
        if bare.status.success() && String::from_utf8_lossy(&bare.stdout).trim() == "true" {
            return Ok(Self {
                root: path.to_path_buf(),
            });
        }
        bail!(
            "{} is not a git repository: {}",
            path.display(),
            String::from_utf8_lossy(&out.stderr).trim()
        )
    }

    fn cmd(&self) -> Command {
        let mut c = Command::new("git");
        // Keep output stable regardless of user config (pager, colors, quoting).
        c.arg("-C").arg(&self.root).args([
            "-c",
            "core.quotepath=off",
            "-c",
            "color.ui=never",
            "--no-pager",
        ]);
        c
    }

    fn run(&self, args: &[&str]) -> Result<String> {
        let out = self
            .cmd()
            .args(args)
            .output()
            .with_context(|| format!("running git {}", args.join(" ")))?;
        if !out.status.success() {
            bail!(
                "git {} failed: {}",
                args.join(" "),
                String::from_utf8_lossy(&out.stderr).trim()
            );
        }
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }

    pub fn resolve(&self, rev: &str) -> Result<String> {
        Ok(self
            .run(&[
                "rev-parse",
                "--verify",
                "--quiet",
                &format!("{rev}^{{commit}}"),
            ])
            .with_context(|| format!("unknown revision {rev:?} (empty repository?)"))?
            .trim()
            .to_string())
    }

    pub fn is_shallow(&self) -> bool {
        self.run(&["rev-parse", "--is-shallow-repository"])
            .map(|s| s.trim() == "true")
            .unwrap_or(false)
    }

    /// Streams commits newest-first. `f` is called for every commit; the git process is reaped afterwards.
    pub fn for_each_commit(
        &self,
        opts: &LogOptions,
        mut f: impl FnMut(crate::parse::Commit) -> Result<()>,
    ) -> Result<()> {
        let mut cmd = self.cmd();
        cmd.args([
            "log",
            "-z",
            "--numstat",
            "-M",
            "--no-merges",
            "--topo-order",
            LOG_FORMAT,
        ]);
        if let Some(s) = &opts.since {
            cmd.arg(format!("--since={s}"));
        }
        if let Some(u) = &opts.until {
            cmd.arg(format!("--until={u}"));
        }
        cmd.arg(&opts.rev).arg("--");
        let mut child = cmd
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("spawning git log")?;
        let stderr = drain(child.stderr.take().expect("piped"));
        let stdout = BufReader::with_capacity(1 << 16, child.stdout.take().expect("piped"));
        let mut result = Ok(());
        for commit in LogParser::new(stdout) {
            if let Err(e) = commit.and_then(&mut f) {
                result = Err(e);
                break;
            }
        }
        finish(child, stderr, "git log", result)
    }

    /// Files at `rev` with their current line count (`None` for binary files). Submodules and symlinks are
    /// skipped. Blob contents are streamed through a single `git cat-file --batch` process.
    pub fn files_at(&self, rev: &str) -> Result<HashMap<String, Option<u64>>> {
        let tree = self
            .cmd()
            .args(["ls-tree", "-r", "-z", "--full-tree", rev])
            .output()?;
        if !tree.status.success() {
            bail!(
                "git ls-tree failed: {}",
                String::from_utf8_lossy(&tree.stderr).trim()
            );
        }
        let mut entries: Vec<(String, String)> = Vec::new(); // (oid, path)
        for rec in tree.stdout.split(|&b| b == 0).filter(|r| !r.is_empty()) {
            let rec = String::from_utf8_lossy(rec);
            let Some((meta, path)) = rec.split_once('\t') else {
                continue;
            };
            let mut m = meta.split(' ');
            let (Some(mode), Some(kind), Some(oid)) = (m.next(), m.next(), m.next()) else {
                continue;
            };
            if kind == "blob" && mode != "120000" {
                entries.push((oid.to_string(), path.to_string()));
            }
        }

        let mut child = self
            .cmd()
            .args(["cat-file", "--batch"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("spawning git cat-file")?;
        let stderr = drain(child.stderr.take().expect("piped"));
        // Write requests on another thread so a full stdout pipe can never deadlock us.
        let mut stdin = child.stdin.take().expect("piped");
        let oids: Vec<String> = entries.iter().map(|(o, _)| o.clone()).collect();
        let writer = thread::spawn(move || -> std::io::Result<()> {
            let mut w = std::io::BufWriter::new(&mut stdin);
            for oid in oids {
                writeln!(w, "{oid}")?;
            }
            w.flush()
        });

        let mut out = BufReader::with_capacity(1 << 16, child.stdout.take().expect("piped"));
        let mut files = HashMap::with_capacity(entries.len());
        let mut header = String::new();
        let mut chunk = vec![0u8; 1 << 16];
        let mut result = Ok(());
        for (_, path) in &entries {
            header.clear();
            if out.read_line(&mut header)? == 0 {
                result = Err(anyhow::anyhow!("git cat-file ended early"));
                break;
            }
            // "<oid> blob <size>\n<content>\n"
            let size: u64 = match header
                .trim_end()
                .rsplit(' ')
                .next()
                .and_then(|s| s.parse().ok())
            {
                Some(s) => s,
                None => {
                    result = Err(anyhow::anyhow!("unexpected cat-file header {header:?}"));
                    break;
                }
            };
            let (mut left, mut lines, mut binary, mut seen, mut last) =
                (size, 0u64, false, 0u64, b'\n');
            while left > 0 {
                let want = left.min(chunk.len() as u64) as usize;
                let n = out.read(&mut chunk[..want])?;
                if n == 0 {
                    bail!("git cat-file output truncated");
                }
                let data = &chunk[..n];
                if seen < 8000 && data[..data.len().min((8000 - seen) as usize)].contains(&0) {
                    binary = true; // same heuristic git uses: a NUL byte near the start
                }
                lines += data.iter().filter(|&&b| b == b'\n').count() as u64;
                last = data[n - 1];
                seen += n as u64;
                left -= n as u64;
            }
            if size > 0 && last != b'\n' {
                lines += 1; // final line without a trailing newline
            }
            let mut nl = [0u8; 1];
            out.read_exact(&mut nl)?;
            files.insert(path.clone(), if binary { None } else { Some(lines) });
        }
        writer
            .join()
            .map_err(|_| anyhow::anyhow!("writer thread panicked"))??;
        finish(child, stderr, "git cat-file", result)?;
        Ok(files)
    }
}

fn drain(mut r: impl Read + Send + 'static) -> thread::JoinHandle<String> {
    thread::spawn(move || {
        let mut s = String::new();
        let _ = r.read_to_string(&mut s);
        s
    })
}

fn finish(
    mut child: Child,
    stderr: thread::JoinHandle<String>,
    what: &str,
    result: Result<()>,
) -> Result<()> {
    if result.is_err() {
        let _ = child.kill();
    }
    let status = child.wait()?;
    let err = stderr.join().unwrap_or_default();
    result?;
    if !status.success() {
        bail!("{what} failed: {}", err.trim());
    }
    Ok(())
}
