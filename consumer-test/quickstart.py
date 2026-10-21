# The README quick start, line for line (readme_sync.py checks this), plus the
# checks around it. run.sh runs it in an empty directory against minigraf
# installed from the wheels or from PyPI.
import json
import os
from minigraf import MiniGrafDb

# In-memory database
db = MiniGrafDb.open_in_memory()
db.execute('(transact [[:alice :name "Alice"] [:alice :age 30]])')

result = json.loads(db.execute("(query [:find ?n ?a :where [?e :name ?n] [?e :age ?a]])"))
print(result["results"])  # [["Alice", 30]]
assert result["results"] == [["Alice", 30]]

# File-backed (persisted to disk)
os.makedirs("path/to", exist_ok=True)
db = MiniGrafDb.open("path/to/mydb.graph")
db.execute('(transact [[:bob :name "Bob"]])')
db.checkpoint()
print("quick start: ok")
