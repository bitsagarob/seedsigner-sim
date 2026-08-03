# Security policy

## What this project is, for threat-model purposes

A simulator. It holds nothing of value, has no accounts, no server and no storage
that survives a reload. Nothing it does is secure and nothing about it is meant to
be: a browser tab on a general-purpose computer is not an air gap, and Pyodide's
in-memory filesystem is not a secure element.

So the interesting risks are not the usual ones. They are:

1. **Someone mistakes it for a wallet.** The single worst outcome is a person
   entering a seed phrase they rely on. Every entry point says so, in plain
   language, and keeping it that way is a security property, not a copy decision.
2. **It reports something that was never there.** A decoder that returns a
   plausible-but-wrong payload could make the wallet display, and offer to back up,
   a seed that was never in front of the camera. A failed scan is recoverable; a
   wrong seed presented as a right one is not. This is why the browser's
   `BarcodeDetector` is used only to decide *whether* a QR is present and is never
   trusted to say *what it contains*; see
   [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#why-rawvalue-is-never-trusted).
3. **Something leaves the page.** There is no backend and the page's CSP permits no
   cross-origin connections. Same-origin requests are permitted and required, to fetch the wallet and the Python runtime. Anything that changes that is a serious bug.
4. **The served wallet stops being the pinned wallet.** The claim that this runs
   real, unmodified SeedSigner rests on `UPSTREAM` plus a build script anyone can
   run and diff. Anything that quietly breaks that reproducibility undermines the
   only claim worth checking.

## In scope

- A route to the wallet that does not carry the "this is a simulator" warning, or a
  change that weakens or hides it.
- Anything that can make the wallet report seed, address or PSBT data that did not
  come from the user's own input, including decoder or shared-buffer handling
  bugs.
- Any outbound network request from the page, any way to load a third-party
  resource, any CSP bypass, XSS or script injection in the pages in `src/web/`.
- Memory-safety-shaped bugs in the shared-buffer channels: out-of-bounds reads or
  writes, length confusion, a payload of one type read as another.
- Anything in `build/` or `UPSTREAM` that would let the served `wallet.zip` differ
  from the pinned commit without that being detectable.
- Wrong or dangerous advice in this repository's documentation, including
  [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md).

## Out of scope

- **"It is not secure."** Correct. It is a simulator: no secure element, no air
  gap, seeds visible in memory and in devtools, nothing encrypted at rest. That is
  the premise, not a finding.
- **Vulnerabilities in SeedSigner itself.** The wallet here is upstream's code, run
  unmodified. Report those to the
  [SeedSigner project](https://github.com/SeedSigner/seedsigner) (or to
  [3rdIteration/seedsigner](https://github.com/3rdIteration/seedsigner), the fork
  this is pinned to). If a fix there matters to this simulator, do tell us so the
  pin can move.
- **Vulnerabilities in Pyodide, jsQR or the browser.** Report upstream. Tell us if
  the exposure here is unusual.
- **Someone else's deployment** missing headers, running an old build or re-skinning
  the page. Take it up with whoever runs it. If our documentation led them there,
  that part is in scope.
- Denial of service against a static page, clickjacking of a page with no state,
  missing rate limits, and reports produced by a scanner without a working exploit.
- Missing hardening headers that have no effect on a page with no origin state and
  no outbound requests.

## Reporting

Use GitHub's private vulnerability reporting on
[bitsagarob/seedsigner-sim](https://github.com/bitsagarob/seedsigner-sim):
**Security → Report a vulnerability**. That channel is private to the maintainers.

If it is not sensitive (a documentation error, a missing warning, a hardening
suggestion), a normal issue is fine and faster.

Please include:

- what you did, in enough detail to repeat it (the `?debug=1` console log is
  usually the most useful thing you can attach);
- the browser and version, and how the page was being served;
- what you expected and what happened instead;
- for a decoder or buffer issue, the input that triggered it.

Never attach a real seed phrase, a real xpub or a real PSBT. Use test vectors:
`test/make_qr_y4m.py` builds material from the standard BIP39 test seed.

## What to expect

This is a small volunteer project. Reports are read and answered as time allows,
in days rather than hours; there is no bounty and no formal SLA. Anything that
falls into categories 1–4 above is treated as urgent. Fixes land on the default
branch, and you will be credited by whatever name you ask for, or not at all if you
prefer.

Only the current state of the default branch is supported. There are no maintained
release branches to backport to.

## If you entered a real seed phrase here

Treat it as compromised, and move the funds to a new seed generated on a device you
trust. The simulator does not transmit or store anything, but it ran in a browser,
on a machine with a network, alongside every extension and every other tab, and
none of that is a place a seed should ever have been. Do not weigh the odds; just
move.
