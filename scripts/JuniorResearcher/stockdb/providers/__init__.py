"""
Providers package.

Each external data source is a `Provider` subclass (see base.py). To add a new
source (e.g. Reddit, X):
  1. Create providers/<name>.py implementing the Provider interface.
  2. Add a row to the `sources` table (name + kind).
  3. Wire it into the orchestrator (or a future social-specific pipeline).

Nothing in the DB schema needs to change to add a source — that is the whole
point of keeping providers behind a common interface.
"""
