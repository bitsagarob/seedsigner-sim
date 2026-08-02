**What this changes, and why**

**How you checked it**
`python3 test/run.py` at minimum. If you touched a hardware seam, say which of the
suite's assertions covers the change — and if none did, that is the first thing to
add.

**Does it change what the simulator claims?**
If it touches the build, the pinned commit, or anything in README/ARCHITECTURE that
a reader is being asked to take on trust, say so explicitly here.
