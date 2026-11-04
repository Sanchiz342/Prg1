//! Metrics computed from a [`History`]: hotspots, authors, knowledge distribution (truck / bus factor) and
//! temporal coupling. Everything here is pure computation, so it is unit-tested without git.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::Serialize;

use crate::history::History;

/// Current line counts by path (`None` = binary), as returned by [`crate::git::Repo::files_at`].
pub type Existing = HashMap<String, Option<u64>>;

// ---- hotspots -----------------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Hotspot {
    pub path: String,
    /// 0–100: recency-weighted change frequency × size, both normalised to the repository maximum.
    pub score: f64,
    pub commits: u32,
    pub recent_commits: f64,
    pub authors: usize,
    pub added: u64,
    pub deleted: u64,
    pub churn: u64,
    pub loc: Option<u64>,
    pub last_change: i64,
}

/// Change frequency × size, after Adam Tornhill's "Your Code as a Crime Scene": files that are both big and
/// frequently changed are where defects and slow-downs concentrate. Each commit counts `0.5^(age / half_life)`
/// with age measured from the newest analysed commit, so old repositories still produce meaningful rankings.
/// Size uses `ln(1 + LOC)` so one giant file does not flatten everything else. Binary files are skipped.
pub fn hotspots(
    h: &History,
    existing: &Existing,
    half_life_days: f64,
    include_deleted: bool,
) -> Vec<Hotspot> {
    let half_life = (half_life_days.max(1.0)) * 86_400.0;
    let mut rows: Vec<Hotspot> = h
        .files
        .iter()
        .filter_map(|f| {
            let loc = match existing.get(&f.path) {
                Some(Some(loc)) => Some(*loc),
                Some(None) => return None, // binary in HEAD
                None if include_deleted => None,
                None => return None,
            };
            let recent: f64 = f
                .times
                .iter()
                .map(|&t| 0.5f64.powf((h.newest - t) as f64 / half_life))
                .sum();
            Some(Hotspot {
                path: f.path.clone(),
                score: 0.0,
                commits: f.commits,
                recent_commits: recent,
                authors: f.by_author.len(),
                added: f.added,
                deleted: f.deleted,
                churn: f.added + f.deleted,
                loc,
                last_change: f.last_time,
            })
        })
        .collect();

    let max_recent = rows.iter().map(|r| r.recent_commits).fold(0.0, f64::max);
    let size = |r: &Hotspot| (1.0 + r.loc.unwrap_or(0) as f64).ln();
    let max_size = rows.iter().map(size).fold(0.0, f64::max);
    for r in &mut rows {
        if max_recent > 0.0 && max_size > 0.0 {
            r.score = (100.0 * (r.recent_commits / max_recent) * (size(r) / max_size) * 10.0)
                .round()
                / 10.0;
        }
        r.recent_commits = (r.recent_commits * 100.0).round() / 100.0;
    }
    rows.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then(b.commits.cmp(&a.commits))
            .then(a.path.cmp(&b.path))
    });
    rows
}

// ---- knowledge: degree of authorship & truck factor --------------------------------------------------------

/// Degree-of-authorship (Fritz et al.) with the coefficients used by Avelino et al., "A novel approach for
/// estimating truck factors" (ICPC 2016):
///
/// `DOA = 3.293 + 1.098·FA + 0.164·DL − 0.321·ln(1 + AC)`
///
/// FA = 1 if the author created the file (oldest commit in the analysed range), DL = the author's commits to
/// the file, AC = commits to the file by everybody else.
pub fn doa(first_author: bool, own: u32, others: u32) -> f64 {
    3.293 + 1.098 * f64::from(u8::from(first_author)) + 0.164 * f64::from(own)
        - 0.321 * (1.0 + f64::from(others)).ln()
}

/// An author "knows" a file when their DOA is at least 75 % of the file's highest DOA and at least 3.293.
pub const DOA_RELATIVE: f64 = 0.75;
pub const DOA_ABSOLUTE: f64 = 3.293;

/// For each file (by index into `h.files`): the authors (indices into `h.authors`) who know it.
pub fn file_authors(h: &History, files: &[usize]) -> HashMap<usize, Vec<usize>> {
    files
        .iter()
        .map(|&fi| {
            let f = &h.files[fi];
            let scores: Vec<(usize, f64)> = f
                .by_author
                .iter()
                .map(|(&a, s)| (a, doa(f.creator == a, s.commits, f.commits - s.commits)))
                .collect();
            let max = scores.iter().map(|s| s.1).fold(f64::MIN, f64::max);
            let mut knowers: Vec<usize> = scores
                .into_iter()
                .filter(|&(_, d)| d >= DOA_ABSOLUTE && d / max >= DOA_RELATIVE)
                .map(|(a, _)| a)
                .collect();
            knowers.sort_unstable();
            (fi, knowers)
        })
        .collect()
}

#[derive(Debug, Clone, Serialize)]
pub struct KeyPerson {
    pub name: String,
    pub email: String,
    /// Files this person is an author of (by DOA).
    pub files: usize,
    /// Share of files still covered after this person and everyone listed before them have left.
    pub coverage_after: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct TruckFactor {
    pub truck_factor: usize,
    pub files: usize,
    /// The authors whose departure (in this order) would leave more than half of the files without an author.
    pub key_people: Vec<KeyPerson>,
}

/// Greedy truck-factor estimation (Avelino et al.): repeatedly remove the author of the most files until fewer
/// than half of the files still have at least one author. The number removed is the truck factor.
pub fn truck_factor(
    h: &History,
    knowers: &HashMap<usize, Vec<usize>>,
    files: &[usize],
) -> TruckFactor {
    let total = files.len();
    let mut count: HashMap<usize, usize> = HashMap::new();
    for fi in files {
        for &a in &knowers[fi] {
            *count.entry(a).or_default() += 1;
        }
    }
    let mut order: Vec<(usize, usize)> = count.into_iter().collect();
    order.sort_by(|x, y| {
        y.1.cmp(&x.1)
            .then_with(|| h.authors[x.0].name.cmp(&h.authors[y.0].name))
    });

    let coverage = |gone: &HashSet<usize>| -> f64 {
        if total == 0 {
            return 0.0;
        }
        let covered = files
            .iter()
            .filter(|fi| knowers[fi].iter().any(|a| !gone.contains(a)))
            .count();
        covered as f64 / total as f64
    };
    let mut gone = HashSet::new();
    let mut key_people = Vec::new();
    for (a, n) in order {
        if coverage(&gone) < 0.5 {
            break;
        }
        gone.insert(a);
        key_people.push(KeyPerson {
            name: h.authors[a].name.clone(),
            email: h.authors[a].email.clone(),
            files: n,
            coverage_after: (coverage(&gone) * 1000.0).round() / 10.0,
        });
    }
    TruckFactor {
        truck_factor: key_people.len(),
        files: total,
        key_people,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct DirectoryRisk {
    pub directory: String,
    pub files: usize,
    pub truck_factor: usize,
    pub key_people: Vec<String>,
}

/// Truck factor per directory prefix of `depth` components ("." for files at the root).
pub fn by_directory(
    h: &History,
    knowers: &HashMap<usize, Vec<usize>>,
    files: &[usize],
    depth: usize,
) -> Vec<DirectoryRisk> {
    let mut groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for &fi in files {
        let parts: Vec<&str> = h.files[fi].path.split('/').collect();
        let dir = if parts.len() <= 1 {
            ".".to_string()
        } else {
            parts[..(parts.len() - 1).min(depth)].join("/")
        };
        groups.entry(dir).or_default().push(fi);
    }
    let mut out: Vec<DirectoryRisk> = groups
        .into_iter()
        .map(|(directory, fs)| {
            let tf = truck_factor(h, knowers, &fs);
            DirectoryRisk {
                directory,
                files: fs.len(),
                truck_factor: tf.truck_factor,
                key_people: tf.key_people.into_iter().map(|p| p.name).collect(),
            }
        })
        .collect();
    out.sort_by(|a, b| {
        a.truck_factor
            .cmp(&b.truck_factor)
            .then(b.files.cmp(&a.files))
            .then(a.directory.cmp(&b.directory))
    });
    out
}

#[derive(Debug, Clone, Serialize)]
pub struct OrphanRisk {
    pub path: String,
    pub authors: Vec<String>,
    pub commits: u32,
    pub loc: Option<u64>,
}

/// Files whose every author is in `leaving` (by email, case-insensitive). With an empty `leaving` list this
/// returns files known by exactly one person — the classic single point of failure.
pub fn orphans(
    h: &History,
    knowers: &HashMap<usize, Vec<usize>>,
    files: &[usize],
    existing: &Existing,
    leaving: &[String],
) -> Vec<OrphanRisk> {
    let leaving: HashSet<String> = leaving.iter().map(|e| e.to_lowercase()).collect();
    let mut out: Vec<OrphanRisk> = files
        .iter()
        .filter(|fi| {
            let k = &knowers[*fi];
            if leaving.is_empty() {
                k.len() == 1
            } else {
                k.is_empty()
                    || k.iter()
                        .all(|&a| leaving.contains(&h.authors[a].email.to_lowercase()))
            }
        })
        .map(|&fi| {
            let f = &h.files[fi];
            OrphanRisk {
                path: f.path.clone(),
                authors: knowers[&fi]
                    .iter()
                    .map(|&a| h.authors[a].name.clone())
                    .collect(),
                commits: f.commits,
                loc: existing.get(&f.path).copied().flatten(),
            }
        })
        .collect();
    out.sort_by(|a, b| {
        b.loc
            .unwrap_or(0)
            .cmp(&a.loc.unwrap_or(0))
            .then(a.path.cmp(&b.path))
    });
    out
}

// ---- authors ------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct AuthorRow {
    pub name: String,
    pub email: String,
    pub commits: u32,
    pub commit_share: f64,
    pub added: u64,
    pub deleted: u64,
    pub files_touched: usize,
    /// Current files this person is an author of (degree of authorship).
    pub files_owned: usize,
    pub first_commit: i64,
    pub last_commit: i64,
}

pub fn authors(h: &History, knowers: &HashMap<usize, Vec<usize>>) -> Vec<AuthorRow> {
    let total: u32 = h.authors.iter().map(|a| a.commits).sum();
    let mut touched = vec![0usize; h.authors.len()];
    for f in &h.files {
        for &a in f.by_author.keys() {
            touched[a] += 1;
        }
    }
    let mut owned = vec![0usize; h.authors.len()];
    for ks in knowers.values() {
        for &a in ks {
            owned[a] += 1;
        }
    }
    let mut rows: Vec<AuthorRow> = h
        .authors
        .iter()
        .enumerate()
        .map(|(i, a)| AuthorRow {
            name: a.name.clone(),
            email: a.email.clone(),
            commits: a.commits,
            commit_share: if total > 0 {
                (1000.0 * f64::from(a.commits) / f64::from(total)).round() / 10.0
            } else {
                0.0
            },
            added: a.added,
            deleted: a.deleted,
            files_touched: touched[i],
            files_owned: owned[i],
            first_commit: a.first_time,
            last_commit: a.last_time,
        })
        .collect();
    rows.sort_by(|a, b| b.commits.cmp(&a.commits).then(a.name.cmp(&b.name)));
    rows
}

// ---- temporal coupling --------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Coupling {
    pub file_a: String,
    pub file_b: String,
    pub shared_commits: u32,
    /// Shared commits / average commits of the two files, in percent.
    pub degree: f64,
}

/// Files that keep changing together are logically coupled even when no import connects them. Commits that
/// touch more than `max_files` files (mass renames, formatting) are ignored: they couple everything.
pub fn coupling(
    h: &History,
    live: &HashSet<usize>,
    min_shared: u32,
    min_degree: f64,
    max_files: usize,
) -> Vec<Coupling> {
    let mut pairs: HashMap<(usize, usize), u32> = HashMap::new();
    for c in &h.commits {
        if c.files.len() < 2 || c.files.len() > max_files {
            continue;
        }
        let mut fs: Vec<usize> = c
            .files
            .iter()
            .copied()
            .filter(|f| live.contains(f))
            .collect();
        fs.sort_unstable();
        for i in 0..fs.len() {
            for j in i + 1..fs.len() {
                *pairs.entry((fs[i], fs[j])).or_default() += 1;
            }
        }
    }
    let mut out: Vec<Coupling> = pairs
        .into_iter()
        .filter(|&(_, n)| n >= min_shared)
        .map(|((a, b), n)| {
            let avg = (f64::from(h.files[a].commits) + f64::from(h.files[b].commits)) / 2.0;
            Coupling {
                file_a: h.files[a].path.clone(),
                file_b: h.files[b].path.clone(),
                shared_commits: n,
                degree: (1000.0 * f64::from(n) / avg).round() / 10.0,
            }
        })
        .filter(|c| c.degree >= min_degree)
        .collect();
    out.sort_by(|a, b| {
        b.degree
            .total_cmp(&a.degree)
            .then(b.shared_commits.cmp(&a.shared_commits))
            .then(a.file_a.cmp(&b.file_a))
    });
    out
}

// ---- summary ------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Language {
    pub extension: String,
    pub files: usize,
    pub loc: u64,
}

pub fn languages(existing: &Existing, files: &[String]) -> Vec<Language> {
    let mut by: HashMap<String, (usize, u64)> = HashMap::new();
    for path in files {
        let Some(Some(loc)) = existing.get(path) else {
            continue;
        };
        let name = path.rsplit('/').next().unwrap_or(path);
        let ext = match name.rsplit_once('.') {
            Some((stem, ext)) if !stem.is_empty() => ext.to_lowercase(),
            _ => "(none)".to_string(),
        };
        let e = by.entry(ext).or_default();
        e.0 += 1;
        e.1 += loc;
    }
    let mut out: Vec<Language> = by
        .into_iter()
        .map(|(extension, (files, loc))| Language {
            extension,
            files,
            loc,
        })
        .collect();
    out.sort_by(|a, b| b.loc.cmp(&a.loc).then(a.extension.cmp(&b.extension)));
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::history::{Filter, History};
    use crate::parse::{Change, Commit};

    fn build(commits: &[(i64, &str, &[&str])]) -> History {
        let filter = Filter::new(None, &[], false, true).unwrap();
        let mut h = History::default();
        for &(time, author, files) in commits {
            h.add(
                Commit {
                    hash: format!("{time}"),
                    author_name: author.into(),
                    author_email: format!("{author}@e"),
                    time,
                    subject: String::new(),
                    changes: files
                        .iter()
                        .map(|p| Change {
                            path: (*p).into(),
                            old_path: None,
                            added: Some(10),
                            deleted: Some(2),
                        })
                        .collect(),
                },
                &filter,
            );
        }
        h
    }

    fn existing(paths: &[(&str, u64)]) -> Existing {
        paths
            .iter()
            .map(|&(p, l)| (p.to_string(), Some(l)))
            .collect()
    }

    #[test]
    fn doa_matches_the_published_formula() {
        assert!((doa(true, 1, 0) - (3.293 + 1.098 + 0.164)).abs() < 1e-9);
        assert!((doa(false, 0, 10) - (3.293 - 0.321 * 11f64.ln())).abs() < 1e-9);
    }

    #[test]
    fn single_author_repository_has_truck_factor_one() {
        let h = build(&[
            (3, "ann", &["a", "b"]),
            (2, "ann", &["c"]),
            (1, "ann", &["a"]),
        ]);
        let files: Vec<usize> = (0..h.files.len()).collect();
        let k = file_authors(&h, &files);
        let tf = truck_factor(&h, &k, &files);
        assert_eq!(tf.truck_factor, 1);
        assert_eq!(tf.key_people[0].name, "ann");
        assert_eq!(tf.key_people[0].coverage_after, 0.0);
    }

    #[test]
    fn knowledge_split_across_people_raises_truck_factor() {
        // Four people each own a quarter of the files: two must leave before coverage drops below 50 %.
        let h = build(&[
            (8, "a", &["a1", "a2"]),
            (7, "b", &["b1", "b2"]),
            (6, "c", &["c1", "c2"]),
            (5, "d", &["d1", "d2"]),
        ]);
        let files: Vec<usize> = (0..h.files.len()).collect();
        let k = file_authors(&h, &files);
        assert_eq!(truck_factor(&h, &k, &files).truck_factor, 3);
        // 3, because after two departures exactly 50 % is still covered (the loop stops only below 50 %).
    }

    #[test]
    fn heavy_later_contributor_becomes_an_author_too() {
        // ann created the file, bob made many more commits: both know it, cid (1 commit) does not.
        let mut commits: Vec<(i64, &str, &[&str])> = vec![(1, "ann", &["f"])];
        for t in 2..20 {
            commits.push((t, "bob", &["f"]));
        }
        commits.push((30, "cid", &["f"]));
        commits.reverse();
        let h = build(&commits);
        let k = file_authors(&h, &[0]);
        let names: Vec<&str> = k[&0].iter().map(|&a| h.authors[a].name.as_str()).collect();
        assert_eq!(names, vec!["bob"]);
        // ann's DOA (3.293 + 1.098 + 0.164 − 0.321·ln 20 ≈ 3.59) is far below 75 % of bob's (≈ 5.9).
    }

    #[test]
    fn orphans_lists_single_owner_files_and_what_if_departures() {
        let h = build(&[
            (3, "ann", &["shared"]),
            (2, "bob", &["shared", "bobs"]),
            (1, "ann", &["anns"]),
        ]);
        let files: Vec<usize> = (0..h.files.len()).collect();
        let k = file_authors(&h, &files);
        let ex = existing(&[("shared", 10), ("bobs", 50), ("anns", 5)]);
        let single: Vec<String> = orphans(&h, &k, &files, &ex, &[])
            .into_iter()
            .map(|o| o.path)
            .collect();
        assert!(single.contains(&"bobs".to_string()) && single.contains(&"anns".to_string()));
        let if_bob_leaves: Vec<String> = orphans(&h, &k, &files, &ex, &["BOB@e".into()])
            .into_iter()
            .map(|o| o.path)
            .collect();
        assert!(if_bob_leaves.contains(&"bobs".to_string()));
        assert!(!if_bob_leaves.contains(&"anns".to_string()));
    }

    #[test]
    fn hotspots_rank_frequent_big_files_first_and_skip_deleted() {
        let h = build(&[
            (300, "a", &["big", "small"]),
            (200, "a", &["big"]),
            (100, "a", &["big", "gone"]),
        ]);
        let ex = existing(&[("big", 1000), ("small", 1000)]);
        let hs = hotspots(&h, &ex, 365.0, false);
        assert_eq!(
            hs.iter().map(|h| h.path.as_str()).collect::<Vec<_>>(),
            vec!["big", "small"]
        );
        assert_eq!(hs[0].score, 100.0);
        assert!(hs[1].score < 50.0);
        assert_eq!(hs[0].churn, 36);
        assert_eq!(hotspots(&h, &ex, 365.0, true).len(), 3);
    }

    #[test]
    fn recency_decay_halves_weight_per_half_life() {
        let day = 86_400;
        let h = build(&[(100 * day, "a", &["new"]), (10 * day, "a", &["old"])]);
        let ex = existing(&[("new", 10), ("old", 10)]);
        let hs = hotspots(&h, &ex, 90.0, false);
        let old = hs.iter().find(|h| h.path == "old").unwrap();
        assert!(
            (old.recent_commits - 0.5).abs() < 0.01,
            "90 days older = half weight, got {}",
            old.recent_commits
        );
    }

    #[test]
    fn coupling_finds_files_that_change_together() {
        let h = build(&[
            (6, "a", &["api.rs", "api_test.rs"]),
            (5, "a", &["api.rs", "api_test.rs"]),
            (4, "a", &["api.rs", "api_test.rs", "readme"]),
            (3, "a", &["api.rs"]),
            (2, "a", &["readme"]),
            (1, "a", &["a", "b", "c", "d"]), // too big, ignored with max_files = 3
        ]);
        let live: HashSet<usize> = (0..h.files.len()).collect();
        let c = coupling(&h, &live, 2, 30.0, 3);
        assert_eq!(c.len(), 1);
        assert_eq!(
            (
                c[0].file_a.as_str(),
                c[0].file_b.as_str(),
                c[0].shared_commits
            ),
            ("api.rs", "api_test.rs", 3)
        );
        assert_eq!(c[0].degree, 85.7); // 3 / ((4 + 3) / 2)
    }

    #[test]
    fn languages_group_by_extension() {
        let ex = existing(&[
            ("src/a.rs", 100),
            ("src/b.RS", 50),
            ("Makefile", 10),
            (".gitignore", 3),
        ]);
        let files: Vec<String> = ex.keys().cloned().collect();
        let l = languages(&ex, &files);
        assert_eq!(
            (l[0].extension.as_str(), l[0].files, l[0].loc),
            ("rs", 2, 150)
        );
        assert!(l.iter().any(|x| x.extension == "(none)" && x.files == 2));
    }
}
