//! gitsight — find hotspots, owners and knowledge risks in a git repository.

mod analysis;
mod git;
mod history;
mod output;
mod parse;

use std::collections::HashSet;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Instant;

use anyhow::Result;
use clap::{Parser, Subcommand, ValueEnum};
use serde::Serialize;

use analysis::Existing;
use git::{LogOptions, Repo};
use history::{Filter, History};
use output::{Align, Style, date, num, span, table};

#[derive(Parser)]
#[command(
    name = "gitsight",
    version,
    about = "Hotspots, authors and bus factor for any git repository",
    long_about = "Reads `git log` and the files at a revision to show where the code changes most (hotspots), \
                  who knows which parts (degree of authorship), how many people the project depends on \
                  (truck / bus factor) and which files change together (temporal coupling).",
    after_help = "Examples:\n  gitsight                                  # summary of the current repository\n  \
                  gitsight hotspots --since '1 year ago' -n 30\n  gitsight bus-factor --depth 2 --without alice@example.com\n  \
                  gitsight coupling --path src/ --format csv > coupling.csv\n  gitsight authors -C ../other-repo --format json"
)]
struct Cli {
    /// Repository to analyse
    #[arg(
        short = 'C',
        long = "repo",
        default_value = ".",
        global = true,
        value_name = "PATH"
    )]
    repo: PathBuf,
    /// Revision to analyse (history reachable from it, files as of it)
    #[arg(long, default_value = "HEAD", global = true)]
    rev: String,
    /// Only commits after this date (anything `git log --since` accepts, e.g. "6 months ago", 2024-01-01)
    #[arg(long, global = true)]
    since: Option<String>,
    /// Only commits before this date
    #[arg(long, global = true)]
    until: Option<String>,
    /// Only files under this directory (applied after following renames)
    #[arg(long, global = true, value_name = "DIR")]
    path: Option<String>,
    /// Glob of files to ignore (repeatable), e.g. --exclude '**/*.generated.ts'
    #[arg(long = "exclude", global = true, value_name = "GLOB")]
    exclude: Vec<String>,
    /// Don't skip lock files, minified assets, vendor/ and node_modules/
    #[arg(long, global = true)]
    no_default_excludes: bool,
    /// Count commits by bots (dependabot[bot], renovate[bot], ...)
    #[arg(long, global = true)]
    include_bots: bool,
    /// Output format
    #[arg(short, long, value_enum, default_value_t = Format::Table, global = true)]
    format: Format,
    /// Maximum rows per table (0 = all)
    #[arg(short = 'n', long, default_value_t = 20, global = true)]
    limit: usize,
    /// Disable colours (also honoured: NO_COLOR environment variable)
    #[arg(long, global = true)]
    no_color: bool,

    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Copy, Clone, PartialEq, Eq, ValueEnum)]
enum Format {
    Table,
    Json,
    Csv,
}

#[derive(Copy, Clone, PartialEq, Eq, ValueEnum)]
enum HotSort {
    Score,
    Commits,
    Churn,
    Authors,
    Loc,
}

#[derive(Subcommand)]
enum Command {
    /// Overview: history, size, bus factor, top hotspots and riskiest directories (default)
    Summary,
    /// Files that change most, weighted by recency and size
    Hotspots {
        /// Commits lose half their weight every N days (relative to the newest analysed commit)
        #[arg(long, default_value_t = 180.0, value_name = "DAYS")]
        half_life: f64,
        #[arg(long, value_enum, default_value_t = HotSort::Score)]
        sort: HotSort,
        /// Also list files that no longer exist at the revision
        #[arg(long)]
        include_deleted: bool,
    },
    /// Contributors with commits, line changes and files they are an author of
    Authors,
    /// Truck factor overall and per directory, plus files known by a single person
    BusFactor {
        /// Directory depth for the per-directory breakdown
        #[arg(long, default_value_t = 1)]
        depth: usize,
        /// What if these people leave? (email, repeatable): lists files left without any author
        #[arg(long, value_name = "EMAIL")]
        without: Vec<String>,
        /// Exit with status 3 if the truck factor is below N (for CI checks)
        #[arg(long, value_name = "N")]
        fail_below: Option<usize>,
    },
    /// Pairs of files that are usually changed in the same commits
    Coupling {
        /// Minimum number of shared commits (low values surface noise from files changed together once or twice)
        #[arg(long, default_value_t = 5)]
        min_shared: u32,
        /// Minimum coupling degree in percent (shared / average commits of the pair)
        #[arg(long, default_value_t = 30.0)]
        min_degree: f64,
        /// Ignore commits touching more files than this (mass reformatting, renames)
        #[arg(long, default_value_t = 30)]
        max_files: usize,
    },
}

struct Ctx {
    cli: Cli,
    style: Style,
    repo_root: String,
    rev: String,
    history: History,
    existing: Existing,
    live: Vec<usize>,
    elapsed_ms: u128,
}

#[derive(Serialize)]
struct Meta<'a> {
    repository: &'a str,
    revision: &'a str,
    commits: usize,
    authors: usize,
    first_commit: Option<String>,
    last_commit: Option<String>,
    files: usize,
    skipped_bot_commits: u64,
}

impl Ctx {
    fn meta(&self) -> Meta<'_> {
        let h = &self.history;
        let has = !h.commits.is_empty();
        Meta {
            repository: &self.repo_root,
            revision: &self.rev,
            commits: h.commits.len(),
            authors: h.authors.len(),
            first_commit: has.then(|| date(h.oldest)),
            last_commit: has.then(|| date(h.newest)),
            files: self.live.len(),
            skipped_bot_commits: h.skipped_bot_commits,
        }
    }

    fn limit<T>(&self, v: Vec<T>) -> Vec<T> {
        if self.cli.limit == 0 {
            v
        } else {
            v.into_iter().take(self.cli.limit).collect()
        }
    }

    fn json(&self, key: &str, value: impl Serialize) -> Result<()> {
        let mut obj = serde_json::Map::new();
        obj.insert("meta".into(), serde_json::to_value(self.meta())?);
        obj.insert(key.into(), serde_json::to_value(value)?);
        println!("{}", serde_json::to_string_pretty(&obj)?);
        Ok(())
    }

    fn emit(
        &self,
        headers: &[&str],
        aligns: &[Align],
        rows: Vec<Vec<String>>,
        flex: Option<usize>,
    ) {
        match self.cli.format {
            Format::Csv => print!("{}", output::csv(headers, &rows)),
            _ => print!(
                "{}",
                table(
                    headers,
                    aligns,
                    &rows,
                    flex,
                    output::terminal_width(),
                    &self.style
                )
            ),
        }
    }

    fn header(&self, title: &str) {
        if self.cli.format == Format::Table {
            let h = &self.history;
            let range = if h.commits.is_empty() {
                String::from("no commits")
            } else {
                format!("{} → {}", date(h.oldest), date(h.newest))
            };
            println!(
                "{}  {}",
                self.style.bold(title),
                self.style.dim(&format!(
                    "{} commits · {} authors · {range} · {} ms",
                    num(h.commits.len() as u64),
                    h.authors.len(),
                    self.elapsed_ms
                ))
            );
            println!();
        }
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match run(cli) {
        Ok(code) => code,
        Err(e) => {
            eprintln!("gitsight: {e:#}");
            ExitCode::from(1)
        }
    }
}

fn run(mut cli: Cli) -> Result<ExitCode> {
    let started = Instant::now();
    let style = Style::detect(cli.no_color);
    let repo = Repo::open(&cli.repo)?;
    let rev = repo.resolve(&cli.rev)?;
    if repo.is_shallow() {
        eprintln!(
            "gitsight: warning: shallow clone — only part of the history is available (run `git fetch --unshallow`)"
        );
    }
    let filter = Filter::new(
        cli.path.clone(),
        &cli.exclude,
        !cli.no_default_excludes,
        cli.include_bots,
    )?;
    let opts = LogOptions {
        rev: rev.clone(),
        since: cli.since.clone(),
        until: cli.until.clone(),
    };
    let history = History::load(&repo, &opts, &filter)?;
    let existing: Existing = repo
        .files_at(&rev)?
        .into_iter()
        .filter(|(p, _)| filter.accepts(p))
        .collect();
    let live = history.live_files(&existing);
    let command = cli.command.take().unwrap_or(Command::Summary);
    let ctx = Ctx {
        repo_root: repo.root.display().to_string(),
        rev: rev.chars().take(12).collect(),
        elapsed_ms: started.elapsed().as_millis(),
        cli,
        style,
        history,
        existing,
        live,
    };
    match command {
        Command::Summary => summary(&ctx)?,
        Command::Hotspots {
            half_life,
            sort,
            include_deleted,
        } => hotspots(&ctx, half_life, sort, include_deleted)?,
        Command::Authors => authors(&ctx)?,
        Command::BusFactor {
            depth,
            without,
            fail_below,
        } => return bus_factor(&ctx, depth, &without, fail_below),
        Command::Coupling {
            min_shared,
            min_degree,
            max_files,
        } => coupling(&ctx, min_shared, min_degree, max_files)?,
    }
    Ok(ExitCode::SUCCESS)
}

fn loc(l: Option<u64>) -> String {
    l.map(num).unwrap_or_else(|| "–".into())
}

fn hotspots(ctx: &Ctx, half_life: f64, sort: HotSort, include_deleted: bool) -> Result<()> {
    let mut rows = analysis::hotspots(&ctx.history, &ctx.existing, half_life, include_deleted);
    match sort {
        HotSort::Score => {}
        HotSort::Commits => rows.sort_by_key(|r| std::cmp::Reverse(r.commits)),
        HotSort::Churn => rows.sort_by_key(|r| std::cmp::Reverse(r.churn)),
        HotSort::Authors => rows.sort_by_key(|r| std::cmp::Reverse(r.authors)),
        HotSort::Loc => rows.sort_by_key(|r| std::cmp::Reverse(r.loc)),
    }
    let rows = ctx.limit(rows);
    if ctx.cli.format == Format::Json {
        return ctx.json("hotspots", &rows);
    }
    ctx.header("Hotspots");
    let st = &ctx.style;
    let table_rows = rows
        .iter()
        .map(|h| {
            let score = format!("{:.1}", h.score);
            vec![
                if h.score >= 50.0 {
                    st.red(&score)
                } else if h.score >= 20.0 {
                    st.yellow(&score)
                } else {
                    score
                },
                h.path.clone(),
                h.commits.to_string(),
                format!("{:.1}", h.recent_commits),
                h.authors.to_string(),
                num(h.churn),
                loc(h.loc),
                date(h.last_change),
            ]
        })
        .collect();
    ctx.emit(
        &[
            "score",
            "file",
            "commits",
            "recent",
            "authors",
            "churn",
            "lines",
            "last change",
        ],
        &[
            Align::Right,
            Align::Left,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Left,
        ],
        table_rows,
        Some(1),
    );
    if ctx.cli.format == Format::Table {
        println!("{}", st.dim(&format!("score = recency-weighted commits (half-life {half_life} days) × ln(lines), both scaled to the top file")));
    }
    Ok(())
}

fn authors(ctx: &Ctx) -> Result<()> {
    let knowers = analysis::file_authors(&ctx.history, &ctx.live);
    let rows = ctx.limit(analysis::authors(&ctx.history, &knowers));
    if ctx.cli.format == Format::Json {
        return ctx.json("authors", &rows);
    }
    ctx.header("Authors");
    let table_rows = rows
        .iter()
        .map(|a| {
            vec![
                a.name.clone(),
                a.email.clone(),
                num(u64::from(a.commits)),
                format!("{:.1}%", a.commit_share),
                format!("+{}", num(a.added)),
                format!("-{}", num(a.deleted)),
                a.files_touched.to_string(),
                a.files_owned.to_string(),
                date(a.first_commit),
                date(a.last_commit),
            ]
        })
        .collect();
    ctx.emit(
        &[
            "name", "email", "commits", "share", "added", "deleted", "touched", "owns", "first",
            "last",
        ],
        &[
            Align::Left,
            Align::Left,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Right,
            Align::Left,
            Align::Left,
        ],
        table_rows,
        Some(1),
    );
    if ctx.cli.format == Format::Table {
        println!("{}", ctx.style.dim("owns = current files this person is an author of (degree of authorship ≥ 75 % of the top author)"));
    }
    Ok(())
}

#[derive(Serialize)]
struct BusFactorReport {
    #[serde(flatten)]
    overall: analysis::TruckFactor,
    directories: Vec<analysis::DirectoryRisk>,
    single_owner_files: usize,
    single_owner: Vec<analysis::OrphanRisk>,
    #[serde(skip_serializing_if = "Option::is_none")]
    without: Option<WhatIf>,
}

#[derive(Serialize)]
struct WhatIf {
    leaving: Vec<String>,
    orphaned_files: usize,
    orphaned_share: f64,
    files: Vec<analysis::OrphanRisk>,
}

fn bus_factor(
    ctx: &Ctx,
    depth: usize,
    without: &[String],
    fail_below: Option<usize>,
) -> Result<ExitCode> {
    let h = &ctx.history;
    let knowers = analysis::file_authors(h, &ctx.live);
    let overall = analysis::truck_factor(h, &knowers, &ctx.live);
    let directories = analysis::by_directory(h, &knowers, &ctx.live, depth.max(1));
    let single = analysis::orphans(h, &knowers, &ctx.live, &ctx.existing, &[]);
    let what_if = (!without.is_empty()).then(|| {
        let files = analysis::orphans(h, &knowers, &ctx.live, &ctx.existing, without);
        let share = if ctx.live.is_empty() {
            0.0
        } else {
            (1000.0 * files.len() as f64 / ctx.live.len() as f64).round() / 10.0
        };
        WhatIf {
            leaving: without.to_vec(),
            orphaned_files: files.len(),
            orphaned_share: share,
            files: ctx.limit(files),
        }
    });
    let tf = overall.truck_factor;
    let report = BusFactorReport {
        overall,
        directories: ctx.limit(directories),
        single_owner_files: single.len(),
        single_owner: ctx.limit(single),
        without: what_if,
    };

    match ctx.cli.format {
        Format::Json => ctx.json("bus_factor", &report)?,
        Format::Csv => {
            let rows = report
                .directories
                .iter()
                .map(|d| {
                    vec![
                        d.directory.clone(),
                        d.files.to_string(),
                        d.truck_factor.to_string(),
                        d.key_people.join("; "),
                    ]
                })
                .collect();
            ctx.emit(
                &["directory", "files", "truck_factor", "key_people"],
                &[Align::Left; 4],
                rows,
                None,
            );
        }
        Format::Table => print_bus_factor(ctx, &report),
    }
    if let Some(min) = fail_below
        && tf < min
    {
        eprintln!("gitsight: truck factor {tf} is below the required {min}");
        return Ok(ExitCode::from(3));
    }
    Ok(ExitCode::SUCCESS)
}

fn print_bus_factor(ctx: &Ctx, r: &BusFactorReport) {
    let st = &ctx.style;
    ctx.header("Bus factor");
    let tf = r.overall.truck_factor;
    let verdict = match tf {
        0 => st.red("0 — no file has a clear author in the analysed range"),
        1 => st.red("1 — one person leaving would orphan most of the code"),
        2 => st.yellow("2 — knowledge rests on two people"),
        n => st.green(&format!("{n}")),
    };
    println!(
        "Truck factor: {verdict}   {}",
        st.dim(&format!("({} files)", r.overall.files))
    );
    if !r.overall.key_people.is_empty() {
        println!();
        let rows: Vec<Vec<String>> = r
            .overall
            .key_people
            .iter()
            .map(|p| {
                vec![
                    p.name.clone(),
                    p.email.clone(),
                    p.files.to_string(),
                    format!("{:.1}%", p.coverage_after),
                ]
            })
            .collect();
        print!(
            "{}",
            table(
                &["key person", "email", "files", "coverage left"],
                &[Align::Left, Align::Left, Align::Right, Align::Right],
                &rows,
                Some(1),
                output::terminal_width(),
                st
            )
        );
    }
    println!();
    let rows: Vec<Vec<String>> = r
        .directories
        .iter()
        .map(|d| {
            let tf = d.truck_factor.to_string();
            vec![
                d.directory.clone(),
                d.files.to_string(),
                if d.truck_factor <= 1 { st.red(&tf) } else { tf },
                d.key_people.join(", "),
            ]
        })
        .collect();
    print!(
        "{}",
        table(
            &["directory", "files", "truck factor", "key people"],
            &[Align::Left, Align::Right, Align::Right, Align::Left],
            &rows,
            Some(0),
            output::terminal_width(),
            st
        )
    );
    println!();
    println!(
        "{} {}",
        st.bold("Single-owner files:"),
        r.single_owner_files
    );
    if !r.single_owner.is_empty() {
        let rows: Vec<Vec<String>> = r
            .single_owner
            .iter()
            .map(|o| {
                vec![
                    o.path.clone(),
                    o.authors.join(", "),
                    o.commits.to_string(),
                    loc(o.loc),
                ]
            })
            .collect();
        print!(
            "{}",
            table(
                &["file", "only author", "commits", "lines"],
                &[Align::Left, Align::Left, Align::Right, Align::Right],
                &rows,
                Some(0),
                output::terminal_width(),
                st
            )
        );
    }
    if let Some(w) = &r.without {
        println!();
        println!(
            "{} {} files ({:.1}%) would have no remaining author",
            st.bold(&format!("If {} leave:", w.leaving.join(", "))),
            w.orphaned_files,
            w.orphaned_share
        );
        for f in &w.files {
            println!("  {}  {}", f.path, st.dim(&loc(f.loc)));
        }
    }
    println!();
    println!("{}", st.dim("Method: degree of authorship + greedy truck factor (Avelino et al., ICPC 2016). See README."));
}

fn coupling(ctx: &Ctx, min_shared: u32, min_degree: f64, max_files: usize) -> Result<()> {
    let live: HashSet<usize> = ctx.live.iter().copied().collect();
    let rows = ctx.limit(analysis::coupling(
        &ctx.history,
        &live,
        min_shared,
        min_degree,
        max_files,
    ));
    if ctx.cli.format == Format::Json {
        return ctx.json("coupling", &rows);
    }
    ctx.header("Temporal coupling");
    let table_rows = rows
        .iter()
        .map(|c| {
            vec![
                format!("{:.0}%", c.degree),
                c.shared_commits.to_string(),
                c.file_a.clone(),
                c.file_b.clone(),
            ]
        })
        .collect();
    ctx.emit(
        &["degree", "shared", "file", "changes with"],
        &[Align::Right, Align::Right, Align::Left, Align::Left],
        table_rows,
        Some(3),
    );
    if ctx.cli.format == Format::Table && rows.is_empty() {
        println!(
            "{}",
            ctx.style.dim(&format!(
                "No pairs with ≥ {min_shared} shared commits and ≥ {min_degree}% degree."
            ))
        );
    }
    Ok(())
}

#[derive(Serialize)]
struct Summary {
    active_authors_90d: usize,
    lines: u64,
    languages: Vec<analysis::Language>,
    truck_factor: analysis::TruckFactor,
    hotspots: Vec<analysis::Hotspot>,
    riskiest_directories: Vec<analysis::DirectoryRisk>,
}

fn summary(ctx: &Ctx) -> Result<()> {
    let h = &ctx.history;
    let knowers = analysis::file_authors(h, &ctx.live);
    let paths: Vec<String> = ctx.existing.keys().cloned().collect();
    let langs = analysis::languages(&ctx.existing, &paths);
    let lines: u64 = langs.iter().map(|l| l.loc).sum();
    let active = h
        .authors
        .iter()
        .filter(|a| h.newest - a.last_time <= 90 * 86_400)
        .count();
    let s = Summary {
        active_authors_90d: active,
        lines,
        languages: langs.into_iter().take(6).collect(),
        truck_factor: analysis::truck_factor(h, &knowers, &ctx.live),
        hotspots: analysis::hotspots(h, &ctx.existing, 180.0, false)
            .into_iter()
            .take(5)
            .collect(),
        riskiest_directories: analysis::by_directory(h, &knowers, &ctx.live, 1)
            .into_iter()
            .take(5)
            .collect(),
    };
    if ctx.cli.format == Format::Json {
        return ctx.json("summary", &s);
    }
    let st = &ctx.style;
    println!(
        "{} {}",
        st.bold("gitsight"),
        st.dim(&format!("{} @ {}", ctx.repo_root, ctx.rev))
    );
    println!();
    if h.commits.is_empty() {
        println!("No commits match the filters.");
        return Ok(());
    }
    let w = 16;
    println!(
        "{:<w$}{} commits by {} authors over {} ({} → {})",
        "History",
        num(h.commits.len() as u64),
        h.authors.len(),
        span(h.newest - h.oldest),
        date(h.oldest),
        date(h.newest)
    );
    println!(
        "{:<w$}{} authors committed in the last 90 days of the range",
        "Active", active
    );
    if h.skipped_bot_commits > 0 {
        println!(
            "{:<w$}{} bot commits skipped (--include-bots to count them)",
            "Bots", h.skipped_bot_commits
        );
    }
    let langs: Vec<String> = s
        .languages
        .iter()
        .map(|l| {
            format!(
                "{} {:.0}%",
                l.extension,
                if lines > 0 {
                    100.0 * l.loc as f64 / lines as f64
                } else {
                    0.0
                }
            )
        })
        .collect();
    println!(
        "{:<w$}{} files, {} lines · {}",
        "Code",
        num(ctx.existing.len() as u64),
        num(lines),
        langs.join(" · ")
    );
    let tf = &s.truck_factor;
    let people: Vec<&str> = tf.key_people.iter().map(|p| p.name.as_str()).collect();
    let tf_text = format!("{}", tf.truck_factor);
    println!(
        "{:<w$}{} {}",
        "Truck factor",
        if tf.truck_factor <= 1 {
            st.red(&tf_text)
        } else if tf.truck_factor == 2 {
            st.yellow(&tf_text)
        } else {
            st.green(&tf_text)
        },
        st.dim(&format!("({})", people.join(", ")))
    );
    println!();
    println!("{}", st.bold("Top hotspots"));
    let rows: Vec<Vec<String>> = s
        .hotspots
        .iter()
        .map(|x| {
            vec![
                format!("{:.1}", x.score),
                x.path.clone(),
                x.commits.to_string(),
                loc(x.loc),
            ]
        })
        .collect();
    print!(
        "{}",
        table(
            &["score", "file", "commits", "lines"],
            &[Align::Right, Align::Left, Align::Right, Align::Right],
            &rows,
            Some(1),
            output::terminal_width(),
            st
        )
    );
    println!();
    println!("{}", st.bold("Riskiest directories"));
    let rows: Vec<Vec<String>> = s
        .riskiest_directories
        .iter()
        .map(|d| {
            vec![
                d.directory.clone(),
                d.files.to_string(),
                d.truck_factor.to_string(),
                d.key_people.join(", "),
            ]
        })
        .collect();
    print!(
        "{}",
        table(
            &["directory", "files", "truck factor", "key people"],
            &[Align::Left, Align::Right, Align::Right, Align::Left],
            &rows,
            Some(0),
            output::terminal_width(),
            st
        )
    );
    println!();
    println!(
        "{}",
        st.dim(&format!(
            "More: gitsight hotspots | authors | bus-factor | coupling   ({} ms)",
            ctx.elapsed_ms
        ))
    );
    Ok(())
}
