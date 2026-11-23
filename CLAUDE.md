# pygmc-ha

Home Assistant custom integration for GQ GMC Geiger counters, built on
[pygmc](https://github.com/Wikilicious/pygmc) (same author).

## Write code like the pygmc author

The reference for style is the pygmc library itself, hand written before AI
tools. Match it, don't polish it into generic AI prose.

**Comments say why, in plain talk.**
- Explain the hardware/spec reason, the bug it dodges, or the user it saves.
  e.g. `# MUST reset buffers... or deal with the bug`.
- Casual and candid is fine: `...`, `i.e.`, `e.g.`, `D.R.Y.`, `T.M.I.`, a bit of
  snark at bad specs or breaking upstream changes. Keep it short; no essays.
- Cite evidence: issue numbers, GQ forum URLs, real device output
  (`ver=GMC-600+Re 2.52`), measured numbers ("200 loops, 13% failed").
- Never invent evidence. No made-up measurements, devices, or issue numbers.

**Docstrings are numpy style** for anything non-trivial:

```python
def probe(port: str, baudrate: int | None = None) -> GMCIdentity:
    """
    Identify the GMC counter on a port.

    Parameters
    ----------
    port: str
        Dev device to probe.
    baudrate: int | None
        Tried first, then every baudrate pygmc knows.

    Returns
    -------
    GMCIdentity

    """
```

One-liners stay one line: `"""Close serial connection."""`. Defaults read as
`Default=None means ...`.

**Code is direct.**
- Flat over clever. Inline a helper that's used once. No generic wrappers
  (`_async_run[T]`) or state machines when an `if` does it.
- Data-driven tables over branching (see `OPTIONAL_READINGS`, and pygmc's
  `_cfg_spec_map` / `device_match_list`), with a comment table explaining them.
- `gc` = geiger counter (the pygmc device object).
- Point the user at the fix when something fails (permissions, brltty,
  bad USB cable) - README troubleshooting + repair issues.

**Home Assistant rules win over the above where they conflict:**
- Logging uses `%s` args, never f-strings (ruff `G`).
- No `µ`/`μ` in comments or docstrings (ruff `RUF002/003`); write `uSv`.
- First docstring line is imperative (`D401`).

## Checks

Run in WSL from the repo root, all must pass before committing:

```bash
source .venv/bin/activate
pytest            # 100% coverage required
ruff check . && ruff format --check .
mypy
```

## Commits

Short, past tense, no ceremony: `Added CPS sensor`, `ruff`, `Lint`,
`History parser bug fixes`. Body only when there's a story worth telling.
