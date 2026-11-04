//! Builds an in-memory model of the analysed history: per-file and per-author statistics keyed by each
//! file's *current* name (renames are followed back through history).

use std::collections::HashMap;

use anyhow::Result;
use globset::{Glob, GlobSet, GlobSetBuilder};

use crate::git::{LogOptions, Repo};
use crate::parse::Commit;

/// Generated or vendored files that dominate churn without telling anything about the code.
pub const DEFAULT_EXCLUDES: &[&str] = &[
    "**/*.lock",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/go.sum",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
    "**/*.snap",
    "vendor/**",
    "**/node_modules/**",
    "dist/**",
];

#[derive(Debug, Clone)]
pub struct Filter {
    pub prefix: Option<String>,
    pub excludes: GlobSet,
    pub include_bots: bool,
}

impl Filter {
    pub fn new(
        prefix: Option<String>,
        excludes: &[String],
        default_excludes: bool,
        include_bots: bool,
    ) -> Result<Self> {
        let mut b = GlobSetBuilder::new();
        let defaults = if default_excludes {
            DEFAULT_EXCLUDES
        } else {
            &[]
        };
        for pattern in defaults
            .iter()
            .copied()
            .chain(excludes.iter().map(String::as_str))
        {
            b.add(
                Glob::new(pattern)
                    .map_err(|e| anyhow::anyhow!("invalid --exclude pattern {pattern:?}: {e}"))?,
            );
        }
        let prefix = prefix
            .map(|p| p.trim_start_matches("./").to_string())
            .filter(|p| !p.is_empty() && p != ".");
        Ok(Self {
            prefix,
            excludes: b.build()?,
            include_bots,
        })
    }

    pub fn accepts(&self, path: &str) -> bool {
        if let Some(prefix) = &self.prefix {
            let dir = prefix.trim_end_matches('/');
            if path != dir && !path.starts_with(&format!("{dir}/")) {
                return false;
            }
        }
        !self.excludes.is_match(path)
    }
}

pub fn is_bot(name: &str, email: &str) -> bool {
    let (n, e) = (name.to_ascii_lowercase(), email.to_ascii_lowercase());
    n.ends_with("[bot]") || e.contains("[bot]@") || n == "dependabot" || n == "renovate-bot"
}

#[derive(Debug, Clone, Default)]
pub struct AuthorFile {
    pub commits: u32,
    pub added: u64,
    pub deleted: u64,
}

#[derive(Debug, Clone)]
pub struct FileStats {
    pub path: String,
    pub commits: u32,
    pub added: u64,
    pub deleted: u64,
    pub binary_changes: u32,
    pub first_time: i64,
    pub last_time: i64,
    /// Commit times touching this file (newest first), used for recency weighting.
    pub times: Vec<i64>,
    /// Author index of the oldest commit touching the file in the analysed range ("first authorship").
    pub creator: usize,
    pub by_author: HashMap<usize, AuthorFile>,
}

#[derive(Debug, Clone)]
pub struct Author {
    pub name: String,
    pub email: String,
    pub commits: u32,
    pub added: u64,
    pub deleted: u64,
    pub first_time: i64,
    pub last_time: i64,
}

#[derive(Debug, Clone)]
pub struct CommitInfo {
    pub files: Vec<usize>,
}

#[derive(Debug, Default)]
pub struct History {
    pub authors: Vec<Author>,
    pub files: Vec<FileStats>,
    pub commits: Vec<CommitInfo>,
    pub scanned_commits: u64,
    pub skipped_bot_commits: u64,
    pub newest: i64,
    pub oldest: i64,
    file_index: HashMap<String, usize>,
    author_index: HashMap<String, usize>,
    /// Old path -> current path, learnt from renames while walking from newest to oldest.
    renames: HashMap<String, String>,
}

impl History {
    pub fn load(repo: &Repo, opts: &LogOptions, filter: &Filter) -> Result<Self> {
        let mut h = History::default();
        repo.for_each_commit(opts, |c| {
            h.add(c, filter);
            Ok(())
        })?;
        Ok(h)
    }

    fn resolve(&self, path: &str) -> String {
        self.renames
            .get(path)
            .cloned()
            .unwrap_or_else(|| path.to_string())
    }

    /// Commits must be added newest first (as `git log` emits them).
    pub fn add(&mut self, c: Commit, filter: &Filter) {
        self.scanned_commits += 1;
        if !filter.include_bots && is_bot(&c.author_name, &c.author_email) {
            self.skipped_bot_commits += 1;
            // Still learn renames so older commits map to the right current names.
            for ch in &c.changes {
                if let Some(old) = &ch.old_path {
                    let current = self.resolve(&ch.path);
                    self.renames.insert(old.clone(), current);
                }
            }
            return;
        }

        let mut touched: Vec<(usize, Option<u32>, Option<u32>)> = Vec::new();
        for ch in &c.changes {
            let current = self.resolve(&ch.path);
            if let Some(old) = &ch.old_path {
                // Older commits that mention `old` are really about today's `current`.
                self.renames.insert(old.clone(), current.clone());
            }
            if !filter.accepts(&current) {
                continue;
            }
            let idx = match self.file_index.get(&current) {
                Some(&i) => i,
                None => {
                    self.files.push(FileStats {
                        path: current.clone(),
                        commits: 0,
                        added: 0,
                        deleted: 0,
                        binary_changes: 0,
                        first_time: c.time,
                        last_time: c.time,
                        times: Vec::new(),
                        creator: 0,
                        by_author: HashMap::new(),
                    });
                    self.file_index.insert(current, self.files.len() - 1);
                    self.files.len() - 1
                }
            };
            if !touched.iter().any(|t| t.0 == idx) {
                touched.push((idx, ch.added, ch.deleted));
            }
        }
        if touched.is_empty() {
            return;
        }

        let key = if c.author_email.is_empty() {
            c.author_name.to_lowercase()
        } else {
            c.author_email.to_lowercase()
        };
        let author = match self.author_index.get(&key) {
            Some(&i) => i,
            None => {
                self.authors.push(Author {
                    name: c.author_name.clone(), // newest name for this identity wins
                    email: c.author_email.clone(),
                    commits: 0,
                    added: 0,
                    deleted: 0,
                    first_time: c.time,
                    last_time: c.time,
                });
                self.author_index.insert(key, self.authors.len() - 1);
                self.authors.len() - 1
            }
        };

        let a = &mut self.authors[author];
        a.commits += 1;
        a.first_time = a.first_time.min(c.time);
        a.last_time = a.last_time.max(c.time);
        for &(idx, added, deleted) in &touched {
            let (add, del) = (
                u64::from(added.unwrap_or(0)),
                u64::from(deleted.unwrap_or(0)),
            );
            a.added += add;
            a.deleted += del;
            let f = &mut self.files[idx];
            f.commits += 1;
            f.added += add;
            f.deleted += del;
            if added.is_none() {
                f.binary_changes += 1;
            }
            f.first_time = f.first_time.min(c.time);
            f.last_time = f.last_time.max(c.time);
            f.times.push(c.time);
            f.creator = author; // overwritten until the oldest commit has been seen
            let af = f.by_author.entry(author).or_default();
            af.commits += 1;
            af.added += add;
            af.deleted += del;
        }

        if self.commits.is_empty() {
            self.newest = c.time;
            self.oldest = c.time;
        }
        self.newest = self.newest.max(c.time);
        self.oldest = self.oldest.min(c.time);
        self.commits.push(CommitInfo {
            files: touched.into_iter().map(|t| t.0).collect(),
        });
    }

    /// Restrict to files that still exist (by current name). Returns indices into `files`.
    pub fn live_files(&self, existing: &HashMap<String, Option<u64>>) -> Vec<usize> {
        (0..self.files.len())
            .filter(|&i| existing.contains_key(&self.files[i].path))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parse::Change;

    fn change(path: &str, old: Option<&str>, added: u32) -> Change {
        Change {
            path: path.into(),
            old_path: old.map(Into::into),
            added: Some(added),
            deleted: Some(0),
        }
    }

    fn commit(time: i64, email: &str, changes: Vec<Change>) -> Commit {
        Commit {
            hash: format!("h{time}"),
            author_name: email.split('@').next().unwrap().into(),
            author_email: email.into(),
            time,
            subject: String::new(),
            changes,
        }
    }

    fn filter() -> Filter {
        Filter::new(None, &[], true, false).unwrap()
    }

    #[test]
    fn follows_rename_chains_back_to_the_current_name() {
        let mut h = History::default();
        let f = filter();
        // Newest first: b.rs -> c.rs, then a.rs -> b.rs, then a.rs created.
        h.add(commit(30, "x@e", vec![change("c.rs", Some("b.rs"), 1)]), &f);
        h.add(commit(20, "y@e", vec![change("b.rs", Some("a.rs"), 2)]), &f);
        h.add(commit(10, "y@e", vec![change("a.rs", None, 50)]), &f);
        assert_eq!(h.files.len(), 1);
        let file = &h.files[0];
        assert_eq!(file.path, "c.rs");
        assert_eq!(file.commits, 3);
        assert_eq!(file.added, 53);
        assert_eq!(h.authors[file.creator].email, "y@e");
        assert_eq!((file.first_time, file.last_time), (10, 30));
    }

    #[test]
    fn a_path_reused_after_a_rename_is_a_different_file() {
        let mut h = History::default();
        let f = filter();
        h.add(commit(30, "x@e", vec![change("old.rs", None, 5)]), &f); // new file at the old path
        h.add(
            commit(20, "x@e", vec![change("new.rs", Some("old.rs"), 1)]),
            &f,
        );
        h.add(commit(10, "x@e", vec![change("old.rs", None, 9)]), &f); // the original
        let paths: Vec<_> = h
            .files
            .iter()
            .map(|f| (f.path.as_str(), f.commits))
            .collect();
        assert_eq!(paths, vec![("old.rs", 1), ("new.rs", 2)]);
    }

    #[test]
    fn filters_excludes_prefix_and_bots() {
        let f = Filter::new(Some("./src/".into()), &["**/*.gen.rs".into()], true, false).unwrap();
        assert!(f.accepts("src/main.rs"));
        assert!(f.accepts("src/deep/x.rs"));
        assert!(!f.accepts("srcfoo/x.rs"));
        assert!(!f.accepts("docs/x.md"));
        assert!(!f.accepts("src/api.gen.rs"));
        assert!(!f.accepts("src/Cargo.lock"));

        let mut h = History::default();
        let all = filter();
        h.add(
            commit(
                10,
                "dependabot[bot]@users.noreply.github.com",
                vec![change("x.rs", None, 1)],
            ),
            &all,
        );
        h.add(
            commit(
                9,
                "me@e",
                vec![change("Cargo.lock", None, 900), change("x.rs", None, 2)],
            ),
            &all,
        );
        assert_eq!(h.skipped_bot_commits, 1);
        assert_eq!(h.files.len(), 1, "lock file excluded by default");
        assert_eq!(h.authors.len(), 1);
        assert_eq!(h.authors[0].added, 2);
    }

    #[test]
    fn identities_are_merged_by_case_insensitive_email() {
        let mut h = History::default();
        let f = filter();
        h.add(
            commit(20, "Ann@Example.com", vec![change("a", None, 1)]),
            &f,
        );
        h.add(
            commit(10, "ann@example.com", vec![change("a", None, 1)]),
            &f,
        );
        assert_eq!(h.authors.len(), 1);
        assert_eq!(h.authors[0].commits, 2);
    }
}
