"""Write executors — the single owner of writes to each domain's tables (Guard 3).

Executors are pure persistence: they receive an AsyncSession (the caller wraps
them in `db.transaction()` = Guard 1) and perform the write. Pre-condition
assertions (Guard 2) live in the tools that call these executors.
"""
