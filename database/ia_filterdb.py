import re
import base64
from struct import pack
from pyrogram.file_id import FileId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from info import FILE_DB_URI, SEC_FILE_DB_URI, DATABASE_NAME, COLLECTION_NAME, MULTIPLE_DATABASE, USE_CAPTION_FILTER

# First Database For File Saving
client = MongoClient(FILE_DB_URI)
db = client[DATABASE_NAME]
col = db[COLLECTION_NAME]

# Second Database For File Saving
sec_client = MongoClient(SEC_FILE_DB_URI)
sec_db = sec_client[DATABASE_NAME]
sec_col = sec_db[COLLECTION_NAME]


async def save_file(media):
    """Save file in the database."""
    file_id = unpack_new_file_id(media.file_id)
    file_name = clean_file_name(media.file_name)

    file_doc = {
        'file_id': file_id,
        'file_name': file_name,
        'file_size': media.file_size,
        'caption': media.caption.html if media.caption else None
    }

    if await is_file_already_saved(file_id, file_name):
        return False, 0

    try:
        col.insert_one(file_doc)
        return True, 1
    except DuplicateKeyError:
        return False, 0
    except Exception:
        if MULTIPLE_DATABASE:
            try:
                sec_col.insert_one(file_doc)
                return True, 1
            except DuplicateKeyError:
                return False, 0
        else:
            print("Database full. Enable MULTIPLE_DATABASE feature to use a secondary DB.")
            return False, 2


def clean_file_name(file_name: str) -> str:
    """Clean and normalize file name."""
    file_name = re.sub(r"[_\-\.\+]", " ", str(file_name))
    for char in ['[', ']', '(', ')', '{', '}']:
        file_name = file_name.replace(char, '')
    return ' '.join(
        w for w in file_name.split()
        if not (w.startswith('@') or w.startswith('http') or w.startswith('www.') or w.startswith('t.me'))
    )


async def is_file_already_saved(file_id, file_name) -> bool:
    """Check if a file exists in either database."""
    filter_doc = {'$or': [{'file_id': file_id}, {'file_name': file_name}]}
    for collection in [col, sec_col] if MULTIPLE_DATABASE else [col]:
        if collection.find_one(filter_doc):
            return True
    return False


async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    """Search files by query."""
    raw_pattern = query_to_regex(query)
    regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    filter_doc = {'file_name': regex}
    files = []

    collections = [col, sec_col] if MULTIPLE_DATABASE else [col]
    for c in collections:
        cursor = c.find(filter_doc).sort('$natural', -1).skip(offset).limit(max_results)
        files.extend(list(cursor))

    total_results = sum(c.count_documents(filter_doc) for c in collections)
    next_offset = offset + max_results if (offset + max_results) < total_results else ""
    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, use_filter=False):
    """Get files matching query or caption (bad files)."""
    raw_pattern = query_to_regex(query)
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except re.error:
        return [], 0

    filter_doc = {'file_name': regex}
    if USE_CAPTION_FILTER:
        filter_doc = {'$or': [{'file_name': regex}, {'caption': regex}]}

    collections = [col, sec_col] if MULTIPLE_DATABASE else [col]
    files = []
    total_results = 0
    for c in collections:
        files.extend(list(c.find(filter_doc)))
        total_results += c.count_documents(filter_doc)
    return files, total_results


async def get_file_details(file_id):
    """Get file document by file_id."""
    return col.find_one({'file_id': file_id}) or (sec_col.find_one({'file_id': file_id}) if MULTIPLE_DATABASE else None)


def query_to_regex(query: str) -> str:
    """Convert query to regex for searching."""
    query = query.strip()
    if not query:
        return '.'
    elif ' ' not in query:
        return rf'(\b|[.+-_]){re.escape(query)}(\b|[.+-_])'
    else:
        return '.*'.join(re.escape(word) for word in query.split())


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


def unpack_new_file_id(new_file_id: str) -> str:
    """Return a normalized file_id."""
    decoded = FileId.decode(new_file_id)
    return encode_file_id(
        pack("<iiqq", int(decoded.file_type), decoded.dc_id, decoded.media_id, decoded.access_hash)
    )
