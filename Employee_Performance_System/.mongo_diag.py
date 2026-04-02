import json
from database.db import mongo_is_configured, get_mongo_database, backup_sqlite_to_mongo, restore_sqlite_from_mongo_if_empty

out = {}
out['mongo_is_configured'] = mongo_is_configured()
db = get_mongo_database()
out['mongo_db_connected'] = bool(db)
out['restore_probe'] = restore_sqlite_from_mongo_if_empty()
out['backup_probe'] = backup_sqlite_to_mongo()
print(json.dumps(out, default=str, indent=2))
