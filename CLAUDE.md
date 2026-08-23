# Project Instructions

## Authoritative Requirements

The complete product requirements and architecture are defined in:

`Backyard_Bird_Discovery_System_Requirements.md`

Read that document before making architectural decisions or implementing
new features.

Do not silently deviate from the requirements. If implementation discoveries
make a requirement impractical, explain the conflict before changing the
architecture.

## Development Principles

1. Think before coding.
2. Prefer simple implementations over unnecessary abstraction.
3. Make small, verifiable changes.
4. Do not modify unrelated code.
5. Do not introduce dependencies without a clear reason.
6. Keep BirdNET behind its adapter.
7. Keep photo-frame integration behind its adapter.
8. Keep image providers behind provider interfaces.
9. Every database schema change requires a migration.
10. Scheduled operations must be idempotent and restart-safe.
11. Audio capture must never depend on internet connectivity.
12. Image or frame failures must never stop bird detection.
13. Use type hints throughout Python.
14. Add tests for new behavior.
15. Never commit credentials or device identifiers.
