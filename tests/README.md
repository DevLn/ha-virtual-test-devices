# Tests

These guard three bugs that each caused **silent data loss** on a real bench.
None of them raised an error when they happened — that is why they are tested
rather than trusted.

| file | guards |
|---|---|
| `test_mutation_queue.py` | Concurrent `create_device` / `remove_device` losing devices. Six concurrent creates once produced **one** device and reported success. Also checks a burst still collapses to a single config-entry reload. |
| `test_file_safety.py` | A failed save destroying the existing device file. The savers used to open the real file in `'w'` (truncating it) and only then serialize, swallowing failures at debug level — one unserialisable value emptied the file and the integration stopped loading. |
| `test_capability_capture.py` | A clone dropping abilities. The capture list is checked **against Home Assistant's own `capability_attributes`**, so a new HA capability shows up as a failure instead of being silently missed. Also checks every capturable key is declared by its platform's schema, and that nothing but primitives reaches the yaml. |
| `test_explicit_ids.py` | An entry with an explicit `entity_id` landing on a different id, or a different identity, on a later load. Also the orphan rule: removing one entry removes **that row** and leaves its device and siblings alone, and a name-keyed file with settled meta loads without being rewritten. |
| `test_device_metadata.py` | The mapping form of a device (`manufacturer`, `model`, `sw_version`, `area`, `entities`) not loading, not reaching the device registry, or being mangled by `create_device` / `remove_device` / `clone_device` rewriting the file. Lists must keep loading exactly as before. |
| `test_export.py` | The exporter writing something the target platform would reject, dropping or mis-grouping an entity, letting a lock or alarm code through, changing anything on the HA it runs on, or `export_only` mode offering more than the one action. The round trip exports one of every domain, loads the file back as a group and checks every entity against its platform's schema. |

## Running them

They need a real Home Assistant, because the behaviour under test lives in the
seam between this integration and HA's own enums and yaml handling. The simplest
way to get one is the HA container:

```bash
docker run -d --name ha-test -p 8124:8123 \
  -v "$PWD/ha-config:/config" ghcr.io/home-assistant/home-assistant:stable

# put the integration and the tests where HA can import them
docker cp custom_components ha-test:/config/custom_components
docker cp tests             ha-test:/config/tests

docker exec ha-test python -m pytest /config/tests -q
```

Expect `80 passed`. There is no `pytest-asyncio` dependency — async cases call
`asyncio.run` directly, so HA and pytest are all you need. The HA image does not
ship pytest, so run `docker exec ha-test pip install pytest` once. On Windows
under Git Bash, prefix the docker commands with `MSYS_NO_PATHCONV=1`, or
`/config` is silently rewritten to a Windows path and nothing is found.

## If you change the capture list

`CLONE_ATTRS` in `clone.py` and the platform schemas are **one contract**: the
platforms validate against closed schemas, so a key a platform does not declare
fails entity creation outright. Change both together, or
`test_every_capturable_key_is_declared_by_its_platform` will tell you.

## Keeping the suite honest

A test that cannot fail is worse than no test. After changing these, break the
thing on purpose and confirm the suite notices — that is how the first version of
this suite was found to be testing nothing at all (the tamper never applied, so
everything "passed"). Known-good result:

| break | failures |
|---|---|
| remove a key from `CLONE_ATTRS` | 4 |
| restore the truncate-before-serialize save | 2 |
| remove the lock in `_mutate_and_reload` | 4 |
| drop the domain check in `explicit_identity` | 1 |
| make the derived unique_id random | 4 |
| skip entity rows in `_async_remove_orphans` | 2 |
| make `_device_block` return no metadata | 2 |
| have `_mutate_and_reload` drop mapping-form devices | 1 |
| skip `_validate` in the exporter | 1 |
| copy `codes` / `last_code_name` in the exporter | 1 |
| bucket exported entities by entity instead of device | 2 |
| drop the `export_only` early return in `async_setup` | 1 |
