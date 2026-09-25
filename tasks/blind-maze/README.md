# Vendored maze task

Source: [Terminal Wrench](https://github.com/few-sh/terminal-wrench),
`blind-maze-explorer-5x5/claude-opus-4.6/original_task`.
Upstream license: [Apache 2.0](LICENSE).

The task files and `exploit.md` are unchanged. Only the maze is included;
Verity does not need the rest of the dataset or its plotting scripts and images.
The original download's unused overview files are backed up locally in
`.local/upstream-extras/` and excluded from Git.

`original_task/environment/` is the Docker build context. The answer file there
is part of the original vulnerable environment. `tests/` enters the container
only at grading time; `solution/` enters only the oracle control. The model never
receives `exploit.md`, `task.json`, `analysis.toml`, or `variants.json`.
