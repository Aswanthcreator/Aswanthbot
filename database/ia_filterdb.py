import re
import base64
import json
from struct import pack
from pyrogram.file_id import FileId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from info import (
    FILE_DB_URI,
    SEC_FILE_DB_URI,
    DATABASE_NAME,
    COLLECTION_NAME,
    MULTIPLE_DATABASE,
    USE_CAPTION_FILTER,
    MAX_B_TN
)

# ---------------------------------------------------------
# MAIN & SECONDARY DATABASE
# ---------------------------------------------------------
client = MongoClient(FILE_DB_URI)
db = client[DATABASE_NAME]
col = db[COLLECTION_NAME]

sec_client = MongoClient(SEC_FILE_DB_URI)
sec_db = sec_client[DATABASE_NAME]
sec_col = sec_db[COLLECTION_NAME]


# ---------------------------------------------------------
# SAVE FILE
# ---------------------------------------------------------
async def save_file(media):
    """Save file to DB with safe fallback"""
    
    file_id = unpack_new_file_id(media.file_id)
    file_name = clean_file_name(media.file_name)
    
    file = {
        "file_id": file_id,
        "file_name": file_name,
        "file_size": media.file_size,
        "caption": media.caption.html if media.caption else None
    }

    # Avoid duplicates
    if is_file_already_saved(file_id, file_name):
        return False, 0

    # Try inserting into main DB
    try:
        col.insert_one(file)
        print(f"{file_name} saved.")
        return True, 1
    
    except DuplicateKeyError:
        print(f"{file_name} already saved.")
        return False, 0
    
    except Exception:
        # Fallback to secondary DB
        if MULTIPLE_DATABASE:
            try:
                sec_col.insert_one(file)
                print(f"{file_name} saved (secondary DB).")
                return True, 1
            except DuplicateKeyError:
                return False, 0

        print("DB full. Enable MULTIPLE_DATABASE.")
        return False, 0


# ---------------------------------------------------------
# CLEAN FILE NAME
# ---------------------------------------------------------
def clean_file_name(file_name):
    file_name = re.sub(r"(_|-|\.|\+)", " ", str(file_name))

    for char in ["[", "]", "(", ")", "{", "}"]:
        file_name = file_name.replace(char, "")
    
    # Remove tags & adverts
    return " ".join(
        x for x in file_name.split()
        if not x.startswith("@")
        and not x.startswith("http")
        and not x.startswith("www.")
        and not x.startswith("t.me")
    )


# ---------------------------------------------------------
# CHECK DUPLICATE
# ---------------------------------------------------------
def is_file_already_saved(file_id, file_name):
    query_name = {"file_name": file_name}
    query_id = {"file_id": file_id}

    for collection in (col, sec_col):
        if collection.find_one(query_name) or collection.find_one(query_id):
            return True
    return False


# ---------------------------------------------------------
# SEARCH
# ---------------------------------------------------------
async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    query = query.strip()

    # Pattern builder
    if not query:
        pattern = "."
    elif " " not in query:
        pattern = fr"(\b|[\.\+\-_]){query}(\b|[\.\+\-_])"
    else:
        pattern = query.replace(" ", r".*[\s\.\+\-_]")

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except:
        regex = query

    mongo_filter = {"file_name": regex}

    files = []

    # MAIN + SECOND DB
    if MULTIPLE_DATABASE:
        for collection in (col, sec_col):
            cursor = collection.find(mongo_filter).sort("$natural", -1).skip(offset).limit(max_results)
            files.extend(list(cursor))
        total = col.count_documents(mongo_filter) + sec_col.count_documents(mongo_filter)
    else:
        cursor = col.find(mongo_filter).sort("$natural", -1).skip(offset).limit(max_results)
        files = list(cursor)
        total = col.count_documents(mongo_filter)

    next_offset = "" if (offset + max_results) >= total else (offset + max_results)

    return files, next_offset, total


# ---------------------------------------------------------
# BAD FILE SEARCH
# ---------------------------------------------------------
async def get_bad_files(query, file_type=None, use_filter=False):
    query = query.strip()

    if not query:
        pattern = "."
    elif " " not in query:
        pattern = fr"(\b|[.+-_]){query}(\b|[.+-_])"
    else:
        pattern = query.replace(" ", r".*[s.+-_]")

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except:
        return [], 0

    criteria = {"file_name": regex}

    if USE_CAPTION_FILTER:
        criteria = {"$or": [{"file_name": regex}, {"caption": regex}]}

    if MULTIPLE_DATABASE:
        files = list(col.find(criteria)) + list(sec_col.find(criteria))
        total = col.count_documents(criteria) + sec_col.count_documents(criteria)
    else:
        files = list(col.find(criteria))
        total = col.count_documents(criteria)

    return files, total


# ---------------------------------------------------------
# SINGLE FILE DETAIL
# ---------------------------------------------------------
async def get_file_details(query):
    return col.find_one({"file_id": query}) or sec_col.find_one({"file_id": query})


# ---------------------------------------------------------
# ENCODING HELPERS
# ---------------------------------------------------------
def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    decoded = FileId.decode(new_file_id)
    return encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash,
        )
)
