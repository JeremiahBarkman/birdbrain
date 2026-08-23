"""Bird image acquisition (§15, Phase 4).

providers/ holds one adapter per image source, all behind the
ImageProvider interface (CLAUDE.md rule 8: keep image providers behind
provider interfaces) — nothing outside providers/ should know how a
specific provider's API works. validation.py and ranking.py are pure
and independently testable. cache.py owns the on-disk layout under
data/images/species/. service.py wires all of it together into the
search -> validate -> rank -> download -> record flow driven by the
`images` CLI commands.

Image failures must never affect detection (CLAUDE.md rule 12) — this
package is only ever invoked from the `images` CLI, never from the
capture or analysis path.
"""
