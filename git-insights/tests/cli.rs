//! End-to-end tests: build small git repositories with scripted authors and dates, run the real binary.

use std::path::Path;
use std::process::{Command, Output};

use serde_json::Value;
use tempfile::TempDir;

const DAY: i64 = 86_400;
const T0: i64 = 1_700_000_000; // 2023-11-14

struct Repo {
    dir: TempDir,
}

impl Repo {
    fn new() -> Self {
        let r = Repo {
            dir: tempfile::tempdir().unwrap(),
        };
        r.git(&["init", "-q", "-b", "main"]);
        r
    }

    fn path(&self) -> &Path {
        self.dir.path()
    }

    fn git(&self, args: &[&str]) -> String {
        self.git_as("Setup", "setup@example.com", T0, args)
    }

    fn git_as(&self, name: &str, email: &str, time: i64, args: &[&str]) -> String {
        let date = format!("@{time} +0000");
        let out = Command::new("git")
            .current_dir(self.path())
            .args(["-c", "commit.gpgsign=false"])
            .args(args)
            .env("GIT_AUTHOR_NAME", name)
            .env("GIT_AUTHOR_EMAIL", email)
            .env("GIT_COMMITTER_NAME", name)
            .env("GIT_COMMITTER_EMAIL", email)
            .env("GIT_AUTHOR_DATE", &date)
            .env("GIT_COMMITTER_DATE", &date)
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        String::from_utf8_lossy(&out.stdout).into_owned()
    }

    fn write(&self, path: &str, content: &str) {
        let p = self.path().join(path);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, content).unwrap();
    }

    /// Writes the files and commits them as `who` (name is the part before '@') at day `day`.
    fn commit(&self, who: &str, day: i64, files: &[(&str, &str)]) {
        for (p, c) in files {
            self.write(p, c);
        }
        self.git(&["add", "-A"]);
        let name = who.split('@').next().unwrap();
        self.git_as(
            name,
            who,
            T0 + day * DAY,
            &["commit", "-q", "-m", &format!("change by {name}")],
        );
    }

    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_gitsight"))
            .arg("-C")
            .arg(self.path())
            .args(args)
            .env("NO_COLOR", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .output()
            .unwrap()
    }

    fn json(&self, args: &[&str]) -> Value {
        let mut all = args.to_vec();
        all.extend(["--format", "json"]);
        let out = self.run(&all);
        assert!(
            out.status.success(),
            "gitsight {args:?} failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        serde_json::from_slice(&out.stdout).unwrap()
    }
}

fn lines(n: usize) -> String {
    (0..n).map(|i| format!("line {i}\n")).collect()
}

fn paths(v: &Value) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x["path"].as_str().unwrap().to_string())
        .collect()
}

#[test]
fn summary_counts_history_and_finds_the_single_owner() {
    let r = Repo::new();
    r.commit(
        "ann@example.com",
        0,
        &[("src/main.rs", &lines(100)), ("README.md", "# demo\n")],
    );
    for d in 1..6 {
        r.commit(
            "ann@example.com",
            d,
            &[("src/main.rs", &lines(100 + d as usize))],
        );
    }
    r.commit("bob@example.com", 7, &[("README.md", "# demo\nmore\n")]);

    let v = r.json(&["summary"]);
    assert_eq!(v["meta"]["commits"], 7);
    assert_eq!(v["meta"]["authors"], 2);
    assert_eq!(v["meta"]["files"], 2);
    assert_eq!(v["meta"]["first_commit"], "2023-11-14");
    assert_eq!(v["summary"]["lines"], 105 + 2);
    assert_eq!(v["summary"]["truck_factor"]["truck_factor"], 1);
    assert_eq!(
        v["summary"]["truck_factor"]["key_people"][0]["email"],
        "ann@example.com"
    );
    assert_eq!(v["summary"]["hotspots"][0]["path"], "src/main.rs");

    let table = String::from_utf8(r.run(&[]).stdout).unwrap();
    assert!(table.contains("7 commits by 2 authors"), "{table}");
}

#[test]
fn renames_are_followed_to_the_current_name() {
    let r = Repo::new();
    r.commit("ann@example.com", 0, &[("old/name.rs", &lines(60))]);
    r.commit(
        "ann@example.com",
        1,
        &[("old/name.rs", &(lines(60) + "x\n"))],
    );
    r.git(&["mv", "old/name.rs", "new name.rs"]);
    r.commit(
        "bob@example.com",
        2,
        &[("new name.rs", &(lines(60) + "x\ny\n"))],
    );

    let v = r.json(&["hotspots"]);
    assert_eq!(paths(&v["hotspots"]), vec!["new name.rs"]);
    assert_eq!(
        v["hotspots"][0]["commits"], 3,
        "history before the rename belongs to the new name"
    );
    assert_eq!(v["hotspots"][0]["authors"], 2);

    let deleted = r.json(&["hotspots", "--include-deleted"]);
    assert_eq!(
        deleted["hotspots"].as_array().unwrap().len(),
        1,
        "the old path is not a separate file"
    );
}

#[test]
fn filters_excludes_and_bots() {
    let r = Repo::new();
    r.commit(
        "ann@example.com",
        0,
        &[
            ("src/a.rs", &lines(10)),
            ("docs/guide.md", &lines(10)),
            ("Cargo.lock", &lines(500)),
        ],
    );
    r.commit(
        "ann@example.com",
        1,
        &[("src/a.rs", &lines(12)), ("src/gen/api.pb.rs", &lines(900))],
    );
    r.commit(
        "dependabot[bot]@users.noreply.github.com",
        2,
        &[("Cargo.lock", &lines(501)), ("src/a.rs", &lines(13))],
    );

    let all = r.json(&["hotspots"]);
    assert!(
        !paths(&all["hotspots"]).contains(&"Cargo.lock".to_string()),
        "lock files excluded by default"
    );
    assert_eq!(all["meta"]["skipped_bot_commits"], 1);
    assert_eq!(all["meta"]["commits"], 2);

    let src = r.json(&["hotspots", "--path", "src", "--exclude", "**/gen/**"]);
    assert_eq!(paths(&src["hotspots"]), vec!["src/a.rs"]);

    let raw = r.json(&["hotspots", "--no-default-excludes", "--include-bots"]);
    assert!(paths(&raw["hotspots"]).contains(&"Cargo.lock".to_string()));
    assert_eq!(raw["meta"]["commits"], 3);
}

#[test]
fn since_limits_the_analysed_window() {
    let r = Repo::new();
    r.commit("old@example.com", 0, &[("a.rs", "1\n")]);
    r.commit("new@example.com", 400, &[("a.rs", "2\n")]);
    let v = r.json(&["authors", "--since", "2024-06-01"]);
    let emails: Vec<&str> = v["authors"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["email"].as_str().unwrap())
        .collect();
    assert_eq!(emails, vec!["new@example.com"]);
}

#[test]
fn mailmap_merges_identities() {
    let r = Repo::new();
    r.commit("ann@old-job.com", 0, &[("a.rs", "1\n")]);
    r.commit("ann@example.com", 1, &[("a.rs", "2\n")]);
    // Canonical name for the current address, plus the old address mapped onto it.
    let mailmap = "Ann Lee <ann@example.com>\nAnn Lee <ann@example.com> <ann@old-job.com>\n";
    r.commit("ann@example.com", 2, &[(".mailmap", mailmap)]);
    let v = r.json(&["authors"]);
    let authors = v["authors"].as_array().unwrap();
    assert_eq!(authors.len(), 1, "{authors:?}");
    assert_eq!(authors[0]["name"], "Ann Lee");
    assert_eq!(authors[0]["commits"], 3);
}

#[test]
fn bus_factor_what_if_and_ci_gate() {
    let r = Repo::new();
    for (i, who) in ["ann", "bob", "cid", "dan"].iter().enumerate() {
        let email = format!("{who}@example.com");
        r.commit(
            &email,
            i as i64,
            &[
                (&format!("{who}/one.rs"), &lines(20)),
                (&format!("{who}/two.rs"), &lines(30)),
            ],
        );
    }
    let v = r.json(&["bus-factor"]);
    assert_eq!(v["bus_factor"]["truck_factor"], 3);
    assert_eq!(v["bus_factor"]["files"], 8);
    assert_eq!(v["bus_factor"]["single_owner_files"], 8);
    assert_eq!(v["bus_factor"]["directories"].as_array().unwrap().len(), 4);

    let what_if = r.json(&[
        "bus-factor",
        "--without",
        "ANN@example.com",
        "--without",
        "bob@example.com",
    ]);
    assert_eq!(what_if["bus_factor"]["without"]["orphaned_files"], 4);
    assert_eq!(what_if["bus_factor"]["without"]["orphaned_share"], 50.0);

    assert_eq!(
        r.run(&["bus-factor", "--fail-below", "3"]).status.code(),
        Some(0)
    );
    let gate = r.run(&["bus-factor", "--fail-below", "4"]);
    assert_eq!(gate.status.code(), Some(3));
    assert!(String::from_utf8_lossy(&gate.stderr).contains("below the required 4"));
}

#[test]
fn coupling_and_csv_output() {
    let r = Repo::new();
    for d in 0..6 {
        r.commit(
            "ann@example.com",
            d,
            &[
                ("api.rs", &lines(10 + d as usize)),
                ("api_test.rs", &lines(5 + d as usize)),
            ],
        );
    }
    r.commit("ann@example.com", 9, &[("other.rs", "x\n")]);
    let v = r.json(&["coupling"]);
    let pairs = v["coupling"].as_array().unwrap();
    assert_eq!(pairs.len(), 1);
    assert_eq!(pairs[0]["shared_commits"], 6);
    assert_eq!(pairs[0]["degree"], 100.0);

    let csv = String::from_utf8(r.run(&["hotspots", "--format", "csv"]).stdout).unwrap();
    let mut rows = csv.lines();
    assert_eq!(
        rows.next().unwrap(),
        "score,file,commits,recent,authors,churn,lines,last change"
    );
    assert!(rows.next().unwrap().contains(",api.rs,6,"));
}

#[test]
fn binary_and_unusual_file_names() {
    let r = Repo::new();
    std::fs::write(r.path().join("logo.png"), [0u8, 1, 2, 3, 0, 255]).unwrap();
    r.commit(
        "ann@example.com",
        0,
        &[
            ("ünïcödé dir/файл з пробілами.rs", &lines(4)),
            ("tab\there.txt", "a\n"),
        ],
    );
    std::fs::write(r.path().join("logo.png"), [0u8, 9, 9]).unwrap();
    r.commit(
        "ann@example.com",
        1,
        &[("ünïcödé dir/файл з пробілами.rs", &lines(6))],
    );
    let v = r.json(&["hotspots"]);
    let p = paths(&v["hotspots"]);
    assert_eq!(p[0], "ünïcödé dir/файл з пробілами.rs");
    assert!(p.contains(&"tab\there.txt".to_string()));
    assert!(
        !p.contains(&"logo.png".to_string()),
        "binary files are not hotspots"
    );
    assert_eq!(v["hotspots"][0]["loc"], 6);
}

#[test]
fn errors_are_reported_with_exit_code_one() {
    let not_repo = tempfile::tempdir().unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_gitsight"))
        .arg("-C")
        .arg(not_repo.path())
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("not a git repository"));

    let empty = Repo::new();
    let out = empty.run(&[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("unknown revision"),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );

    let r = Repo::new();
    r.commit("ann@example.com", 0, &[("a", "1\n")]);
    assert_eq!(r.run(&["--exclude", "[unclosed"]).status.code(), Some(1));
    assert_eq!(r.run(&["--rev", "no-such-branch"]).status.code(), Some(1));
    assert_eq!(
        r.run(&["no-such-command"]).status.code(),
        Some(2),
        "usage errors exit with 2 (clap)"
    );
}
