#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Advanced AI Engine v4.0
- Multi-layer command correction
- Context-aware suggestions
- Learning from usage
- Multiple ranked suggestions
"""

import difflib
import json
import os
import re
import shlex
import subprocess
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# CONFIGURATION
# ============================================================

DATABASE_FILE = "advanced_database.db"
LEARNING_FILE = ".ai_learning.json"
HISTORY_FILE = ".ai_history.json"
MAX_HISTORY = 200
MAX_SUGGESTIONS = 5

# Termux-specific command preferences
TERMUX_PREFERRED = {
    "install": "pkg",
    "update": "pkg",
    "search": "pkg",
    "python": "python3",
    "python2": "python3",
}


# ============================================================
# TYPES
# ============================================================

Record = Dict[str, str]


# ============================================================
# LAYER 1: SMART PREPROCESSING
# ============================================================

# Common abbreviations users type
ABBREVIATIONS = {
    "ll": "ls -la",
    "la": "ls -A",
    "l": "ls",
    "grep": "grep",
    "ps": "ps",
    "gd": "git diff",
    "gs": "git status",
    "gc": "git commit",
    "ga": "git add",
    "gp": "git push",
    "gl": "git log",
    "dc": "docker compose",
    "dk": "docker",
    "k": "kubectl",
    "tf": "terraform",
}

# Common typos (keyboard-adjacent)
COMMON_TYPOS = {
    "lst": "ls",
    "lss": "ls",
    "laa": "ls",
    "catt": "cat",
    "catt": "cat",
    "mkdri": "mkdir",
    "mkdri": "mkdir",
    "tuch": "touch",
    "toch": "touch",
    "grpe": "grep",
    "gerp": "grep",
    "gerp": "grep",
    "gti": "git",
    "gti": "git",
    "pyhton": "python",
    "pyton": "python",
    "pytohn": "python",
    "ecoh": "echo",
    "ehco": "echo",
    "pwd": "pwd",
    "pwe": "pwd",
}


def expand_abbreviations(text: str) -> str:
    """Expand common abbreviations before analysis."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return text
    first = parts[0].lower()
    if first in ABBREVIATIONS:
        rest = parts[1] if len(parts) > 1 else ""
        return (ABBREVIATIONS[first] + " " + rest).strip()
    return text


def fix_common_typos(text: str) -> str:
    """Fix very common typos in the first token."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return text
    first = parts[0].lower()
    if first in COMMON_TYPOS:
        rest = parts[1] if len(parts) > 1 else ""
        return (COMMON_TYPOS[first] + " " + rest).strip()
    return text


def normalize_text(text: str, keep_dash: bool = True) -> str:
    """
    Normalize text for comparison.
    keep_dash=True preserves hyphens so apt-get != apt get
    """
    text = text.lower().strip()

    if keep_dash:
        text = re.sub(r"[_/\\]+", " ", text)
    else:
        text = re.sub(r"[_/\\-]+", " ", text)

    text = re.sub(r"\s+", " ", text)
    return text

# ============================================================
# LAYER 2: FUZZY MATCHING
# ============================================================

def text_similarity(first: str, second: str) -> float:
    """Sequence-based similarity."""
    first = normalize_text(first)
    second = normalize_text(second)

    if not first or not second:
        return 0.0

    return difflib.SequenceMatcher(None, first, second).ratio()


def token_overlap(first: str, second: str) -> float:
    """Jaccard similarity on tokens."""
    first_words = set(re.findall(r"[A-Za-z0-9_+-]+", normalize_text(first)))
    second_words = set(re.findall(r"[A-Za-z0-9_+-]+", normalize_text(second)))

    if not first_words or not second_words:
        return 0.0

    intersection = len(first_words & second_words)
    union = len(first_words | second_words)
    return intersection / union if union else 0.0


def partial_ratio(first: str, second: str) -> float:
    """
    Find best matching substring.
    Useful when user types part of a command.
    """
    first = normalize_text(first)
    second = normalize_text(second)

    if not first or not second:
        return 0.0

    if len(first) > len(second):
        first, second = second, first

    best = 0.0
    window = len(first)

    for i in range(len(second) - window + 1):
        chunk = second[i:i + window]
        ratio = difflib.SequenceMatcher(None, first, chunk).ratio()
        if ratio > best:
            best = ratio

    return best

# ============================================================
# LAYER 3: PHONETIC MATCHING
# ============================================================

def phonetic_key(word: str) -> str:
    """
    Simplified Soundex-like algorithm.
    Handles common phonetic typos: gti -> git
    """
    word = word.lower()
    if not word:
        return ""

    # Keep first letter
    first = word[0]

    # Replace phonetic equivalents
    replacements = [
        ("ph", "f"),
        ("ck", "k"),
        ("qu", "k"),
        ("x", "ks"),
        ("wh", "w"),
        ("gh", "g"),
        ("sh", "s"),
        ("ch", "c"),
        ("th", "t"),
    ]

    body = word[1:]
    for old, new in replacements:
        body = body.replace(old, new)

    # Remove vowels (except first) and duplicate consonants
    result = first
    prev = first
    for ch in body:
        if ch in "aeiou":
            continue
        if ch != prev:
            result += ch
            prev = ch

    return result[:8]


def phonetic_similarity(first: str, second: str) -> float:
    """Compare using phonetic keys."""
    a = phonetic_key(first)
    b = phonetic_key(second)

    if not a or not b:
        return 0.0

    return difflib.SequenceMatcher(None, a, b).ratio()

# ============================================================
# LAYER 4: SEMANTIC SCORING (Value Kinds)
# ============================================================

def value_kind(token: str) -> str:
    """Guess the type of a value supplied by the user."""
    token = token.strip()

    if not token:
        return "empty"

    # Operators
    if token in ("&&", "||", "|", ";", "&"):
        return "operator"

    # Redirects
    if token in (">", ">>", "<", "2>", "2>>", "&>"):
        return "redirect"

    # Options
    if token.startswith("-") and token != "-":
        return "option"

    # Numbers
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return "number"

    # URLs
    if re.match(r"^https?://", token):
        return "url"

    # IP addresses
    if re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?", token):
        return "ip"

    # Ports (number after :)
    if re.fullmatch(r":\d+", token):
        return "port"

    # Time durations
    if re.fullmatch(r"\d+[smhdw]", token):
        return "time"

    # Regex patterns (contain special chars)
    if any(c in token for c in "[](){}*+?^$\\"):
        return "regex"

    # Directory
    if token.endswith("/") or (
        "/" in token and "." not in token.rsplit("/", 1)[-1]
    ):
        return "directory"

    # File with extension
    if re.search(r"\.[A-Za-z0-9_-]+$", token):
        return "file"

    # Path
    if "/" in token:
        return "path"

    return "word"


def pattern_kind(token: str) -> str:
    """Guess the expected value type from a syntax token."""
    token = token.strip().lower()
    token = token.strip("<>()[]{}")
    token = token.replace("...", "")

    # Options
    if token.startswith("-") or any(w in token for w in ("option", "flag", "flags")):
        return "option"

    # Directory
    if any(w in token for w in ("directory", "folder", "dir")):
        return "directory"

    # Path
    if "path" in token:
        return "path"

    # URL
    if any(w in token for w in ("url", "uri", "link")):
        return "url"

    # IP
    if any(w in token for w in ("ip", "host", "hostname")):
        return "ip"

    # Port
    if "port" in token:
        return "port"

    # Regex
    if any(w in token for w in ("pattern", "regex", "expression")):
        return "regex"

    # Time
    if any(w in token for w in ("time", "duration", "interval", "timeout")):
        return "time"

    # File
    if any(w in token for w in ("file", "filename", "script", "archive", "input", "output")):
        return "file"

    # Number
    if any(w in token for w in ("number", "count", "pid", "size", "n")):
        return "number"

    # Operator
    if token in ("&&", "||", "|", ";", "&"):
        return "operator"

    return "word"


def kind_similarity(user_kind: str, syntax_kind: str) -> float:
    """Compare kind of user value with expected kind."""
    if user_kind == syntax_kind:
        return 1.0

    # Related kinds with weights
    related = {
        frozenset(("file", "path")): 0.90,
        frozenset(("directory", "path")): 0.85,
        frozenset(("word", "file")): 0.60,
        frozenset(("word", "path")): 0.60,
        frozenset(("word", "directory")): 0.55,
        frozenset(("number", "port")): 0.85,
        frozenset(("word", "regex")): 0.50,
        frozenset(("word", "url")): 0.50,
        frozenset(("word", "ip")): 0.50,
        frozenset(("number", "time")): 0.70,
        frozenset(("option", "word")): 0.30,
    }

    return related.get(frozenset((user_kind, syntax_kind)), 0.0)


# ============================================================
# LAYER 5: STRUCTURE SIMILARITY (DP)
# ============================================================

def parse_syntax_tokens(syntax: str) -> List[str]:
    """Parse command syntax and remove the command name."""
    tokens = split_input(syntax)
    if tokens is None:
        tokens = syntax.split()
    if len(tokens) <= 1:
        return []
    return tokens[1:]


def structure_similarity(user_parts: List[str], syntax: str) -> float:
    """
    Compare structural shape via dynamic programming.
    Now with adaptive weights.
    """
    user_kinds = [value_kind(t) for t in user_parts[1:]]
    syntax_kinds = [pattern_kind(t) for t in parse_syntax_tokens(syntax)]

    user_count = len(user_kinds)
    syntax_count = len(syntax_kinds)

    if syntax_count == 0:
        return 1.0 if user_count == 0 else 0.0

    # DP table
    table = [[0.0] * (syntax_count + 1) for _ in range(user_count + 1)]

    # Adaptive penalties
    delete_penalty = 0.30
    insert_penalty = 0.20

    for i in range(1, user_count + 1):
        table[i][0] = max(0.0, table[i - 1][0] - delete_penalty)

    for j in range(1, syntax_count + 1):
        table[0][j] = max(0.0, table[0][j - 1] - insert_penalty)

    for i in range(1, user_count + 1):
        for j in range(1, syntax_count + 1):
            match_score = table[i - 1][j - 1] + kind_similarity(
                user_kinds[i - 1], syntax_kinds[j - 1]
            )
            delete_score = table[i - 1][j] - delete_penalty
            insert_score = table[i][j - 1] - insert_penalty
            table[i][j] = max(0.0, match_score, delete_score, insert_score)

    maximum_length = max(user_count, syntax_count)
    if maximum_length == 0:
        return 1.0

    return max(0.0, min(1.0, table[user_count][syntax_count] / maximum_length))


# ============================================================
# LAYER 6: RECORD SCORING
# ============================================================

def split_input(text: str) -> Optional[List[str]]:
    """Safely split shell-like input."""
    try:
        return shlex.split(text)
    except ValueError:
        return None


def record_text_score(user_input: str, record: Record) -> float:
    """Compare user input with all record fields."""
    candidates = [
        record.get("name", ""),
        record.get("syntax", ""),
        record.get("description", ""),
        record.get("example", ""),
        record.get("category", ""),
    ]

    scores: List[float] = []
    for candidate in candidates:
        if not candidate:
            continue
        scores.append(text_similarity(user_input, candidate))
        scores.append(token_overlap(user_input, candidate))
        scores.append(partial_ratio(user_input, candidate))

    return max(scores) if scores else 0.0


# ============================================================
# LAYER 7: CONTEXT AWARENESS
# ============================================================

def detect_context(user_input: str) -> str:
    """
    Detect the context of the command.
    Returns: package, git, file, network, system, unknown
    """
    text = user_input.lower()

    if any(w in text for w in ("install", "update", "upgrade", "remove", "search", "pkg", "apt")):
        return "package"
    if any(w in text for w in ("git", "commit", "push", "pull", "merge", "branch", "clone")):
        return "git"
    if any(w in text for w in ("cat", "nano", "vim", "less", "head", "tail", "touch", "rm", "cp", "mv")):
        return "file"
    if any(w in text for w in ("curl", "wget", "ping", "ssh", "scp", "http", "https")):
        return "network"
    if any(w in text for w in ("ps", "kill", "top", "free", "df", "du", "uname", "whoami")):
        return "system"

    return "unknown"


def context_boost(record: Record, context: str) -> float:
    """
    Boost score based on context match.
    Returns multiplier between 0.9 and 1.15
    """
    if context == "unknown":
        return 1.0

    record_category = record.get("category", "").lower()
    record_name = record.get("name", "").lower()

    if context == "package" and record_category in ("package", "termux"):
        return 1.15
    if context == "git" and "git" in record_name:
        return 1.15
    if context == "file" and record_category in ("filesystem", "text"):
        return 1.10
    if context == "network" and record_category == "network":
        return 1.15
    if context == "system" and record_category in ("system", "process"):
        return 1.10

    return 1.0


# ============================================================
# LAYER 8: LEARNING FROM USAGE
# ============================================================

class LearningStore:
    """Persistent learning store for usage statistics."""

    def __init__(self, path: str = LEARNING_FILE):
        self.path = path
        self.data = self._load()

    def _load(self) -> Dict:
        if not os.path.exists(self.path):
            return {"commands": {}, "suggestions": {}, "corrections": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"commands": {}, "suggestions": {}, "corrections": {}}

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def record_command(self, command: str) -> None:
        """Record a successfully used command."""
        self.data.setdefault("commands", {})
        self.data["commands"][command] = self.data["commands"].get(command, 0) + 1
        self.save()

    def record_correction(self, wrong: str, correct: str) -> None:
        """Record a correction the user accepted."""
        self.data.setdefault("corrections", {})
        self.data["corrections"][wrong.lower()] = correct
        self.save()

    def get_frequency_boost(self, command: str) -> float:
        """Get boost factor based on usage frequency."""
        count = self.data.get("commands", {}).get(command, 0)
        # Logarithmic boost: 1.0 + log(1+count)/10
        import math
        return 1.0 + math.log1p(count) / 10.0

    def get_known_correction(self, command: str) -> Optional[str]:
        """Check if we've seen this exact error before."""
        return self.data.get("corrections", {}).get(command.lower())


# ============================================================
# DATABASE LOADING
# ============================================================

def load_command_records(database_file: str = DATABASE_FILE) -> List[Record]:
    """Load complete command records from the database."""
    records: List[Record] = []

    try:
        with open(database_file, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()

                if not line or line.startswith("#"):
                    continue

                parts = line.split("|", 8)

                if len(parts) < 9 or parts[0].strip() != "C":
                    continue

                record = {
                    "id": parts[1].strip(),
                    "name": parts[2].strip(),
                    "type": parts[3].strip(),
                    "category": parts[4].strip(),
                    "syntax": parts[5].strip(),
                    "description": parts[6].strip(),
                    "example": parts[7].strip(),
                    "package": parts[8].strip(),
                }

                if not record["name"]:
                    continue

                records.append(record)

    except FileNotFoundError:
        print(f"Database file not found: {database_file}")
        return []
    except OSError as error:
        print(f"Database error: {error}")
        return []

    return records


def build_index(records: List[Record]) -> Dict[str, Record]:
    """Build a name -> record index for fast lookup."""
    index = {}
    for record in records:
        name = record["name"].lower()
        index[name] = record
    return index


# ============================================================
# RANKING ENGINE
# ============================================================

def rank_records(
    user_input: str,
    records: List[Record],
    learning: Optional[LearningStore] = None,
) -> List[Tuple[float, Record]]:
    """
    Rank all database commands using multiple analysis layers.
    """
    parts = split_input(user_input)
    if not parts:
        return []

    typed_command = parts[0].lower()
    context = detect_context(user_input)

    ranked: List[Tuple[float, Record]] = []

    for record in records:
        command_name = record["name"].lower()

        # Layer 2: fuzzy
        name_score = text_similarity(typed_command, command_name)
        partial_score = partial_ratio(typed_command, command_name)

        # Layer 3: phonetic
        phonetic_score = phonetic_similarity(typed_command, command_name)

        # Exact match
        if command_name == typed_command:
            name_score = 1.0

        # Layer 5: structure
        structure_score = structure_similarity(parts, record["syntax"])

        # Layer 6: full text
        full_text_score = record_text_score(user_input, record)

        # Layer 7: context
        ctx_boost = context_boost(record, context)

        # Layer 8: learning
        freq_boost = 1.0
        if learning:
            freq_boost = learning.get_frequency_boost(record["name"])

        # Weighted combination
        if name_score >= 0.80:
            final_score = (
                name_score * 0.50
                + partial_score * 0.10
                + phonetic_score * 0.05
                + structure_score * 0.20
                + full_text_score * 0.15
            )
        else:
            final_score = (
                name_score * 0.30
                + partial_score * 0.10
                + phonetic_score * 0.10
                + structure_score * 0.30
                + full_text_score * 0.20
            )

        final_score *= ctx_boost
        final_score *= freq_boost

        ranked.append((final_score, record))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def build_corrected_command(record: Record, user_parts: List[str]) -> str:
    """Replace only the command name. Keep all user arguments untouched."""
    corrected_parts = [record["name"]] + user_parts[1:]
    return " ".join(shlex.quote(part) for part in corrected_parts)


def suggest_commands(
    user_input: str,
    records: List[Record],
    learning: Optional[LearningStore] = None,
    max_results: int = MAX_SUGGESTIONS,
) -> List[Tuple[float, str, Record]]:
    """
    Return top N suggestions with confidence scores.
    """
    parts = split_input(user_input)
    if not parts or not records:
        return []

    typed_command = parts[0].lower()
    ranked = rank_records(user_input, records, learning)

    suggestions: List[Tuple[float, str, Record]] = []

    for score, record in ranked:
        if len(suggestions) >= max_results:
            break

        best_name = record["name"].lower()

        # Skip if it's the same command (no correction needed)
        if best_name == typed_command:
            continue

        # Reject weak guesses
        if score < 0.45:
            continue

        # Build corrected command
        corrected = build_corrected_command(record, parts)
        suggestions.append((score, corrected, record))

    return suggestions


def explain_suggestion(
    score: float,
    record: Record,
    user_input: str,
) -> str:
    """Generate a human-readable explanation for a suggestion."""
    parts = split_input(user_input)
    if not parts:
        return ""

    typed = parts[0]
    correct = record["name"]

    reasons = []

    # Name similarity
    sim = text_similarity(typed, correct)
    if sim >= 0.80:
        reasons.append("very similar name")
    elif sim >= 0.60:
        reasons.append("similar name")
    elif phonetic_similarity(typed, correct) >= 0.70:
        reasons.append("phonetically similar")

    # Structure
    struct = structure_similarity(parts, record["syntax"])
    if struct >= 0.80:
        reasons.append("matches expected structure")

    # Category
    if record.get("category"):
        reasons.append(f"category: {record['category']}")

    # Confidence level
    if score >= 0.85:
        confidence = "high"
    elif score >= 0.65:
        confidence = "medium"
    else:
        confidence = "low"

    reason_text = ", ".join(reasons) if reasons else "best match"
    return f"[{confidence}] {reason_text}"
    
    
# ============================================================
# AUTO-DISCOVERY
# ============================================================

def discover_installed_commands():
    """اكتشف كل الأوامر المثبتة على النظام."""
    path_dirs = os.environ.get("PATH", "").split(":")
    commands = set()
    
    for directory in path_dirs:
        if os.path.isdir(directory):
            for entry in os.listdir(directory):
                full_path = os.path.join(directory, entry)
                if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                    commands.add(entry)
    
    return sorted(commands)

def read_command_help(command: str) -> dict:
    """اقرأ --help واستخرج المعلومات."""
    import subprocess
    
    info = {
        "name": command,
        "description": "",
        "options": [],
        "usage": "",
    }
    
    # جرّب --help ثم -h ثم help
    for flag in ["--help", "-h", "help"]:
        try:
            result = subprocess.run(
                [command, flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                output = result.stdout or result.stderr
                # استخرج الوصف
                lines = output.split("\n")
                for line in lines[:20]:
                    if line.strip() and not line.startswith("Usage"):
                        info["description"] = line.strip()
                        break
                # استخرج Usage
                for line in lines:
                    if "usage:" in line.lower():
                        info["usage"] = line.strip()
                        break
                break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    
    return info
    
def auto_add_to_database(command: str, info: dict) -> bool:
    """أضف الأمر الجديد إلى قاعدة البيانات."""
    database_file = "advanced_database.db"
    
    # اقرأ آخر ID
    last_id = 0
    with open(database_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("C|"):
                try:
                    current_id = int(line.split("|")[1])
                    last_id = max(last_id, current_id)
                except (ValueError, IndexError):
                    continue
    
    # أنشئ سطراً جديداً
    new_id = f"{last_id + 1:04d}"
    new_line = (
        f"C|{new_id}|{command}|external|discovered|"
        f"{info.get('usage', command)}|"
        f"{info.get('description', 'Auto-discovered command')}|"
        f"{command} --help|discovered\n"
    )
    
    # أضف السطر
    try:
        with open(database_file, "a", encoding="utf-8") as f:
            f.write(new_line)
        return True
    except OSError:
        return False

# Commands to skip during discovery
SKIP_COMMANDS = {
    # Termux internal
    "bash", "sh", "dash", "zsh", "fish",
    # System
    "init", "logd", "servicemanager", "vold",
    # Editors (interactive)
    "vi", "vim", "nano", "emacs", "ed",
    # Interactive shells
    "login", "su", "sudo",
    # Network daemons
    "sshd", "telnetd", "ftpd",
    # Other
    "dalvikvm", "app_process", "linker",
}

# Minimum description length to be useful
MIN_DESCRIPTION_LENGTH = 10


def discover_installed_commands() -> List[str]:
    """
    Scan PATH directories and return all executable commands.
    """
    path_var = os.environ.get("PATH", "")
    path_dirs = path_var.split(os.pathsep)

    commands = set()

    for directory in path_dirs:
        if not directory or not os.path.isdir(directory):
            continue

        try:
            entries = os.listdir(directory)
        except OSError:
            continue

        for entry in entries:
            full_path = os.path.join(directory, entry)

            # Skip directories
            if not os.path.isfile(full_path):
                continue

            # Skip non-executable
            if not os.access(full_path, os.X_OK):
                continue

            # Skip known internal commands
            if entry in SKIP_COMMANDS:
                continue

            # Skip names with weird characters
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", entry):
                continue

            commands.add(entry)

    return sorted(commands)


def read_command_help(command: str) -> Dict[str, str]:
    """
    Run 'command --help' and extract useful information.
    Returns dict with keys: name, description, usage, options
    """
    info = {
        "name": command,
        "description": "",
        "usage": "",
        "options": "",
    }

    # Try different help flags
    help_flags = [
        ["--help"],
        ["-h"],
        ["help"],
        [],
    ]

    output = ""

    for flags in help_flags:
        try:
            result = subprocess.run(
                [command] + flags,
                capture_output=True,
                text=True,
                timeout=3,
                stdin=subprocess.DEVNULL,
            )

            candidate = (result.stdout or "") + (result.stderr or "")

            if candidate.strip():
                output = candidate
                break

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    if not output:
        return info

    lines = output.split("\n")

    # Extract description (first meaningful line)
    for line in lines[:30]:
        stripped = line.strip()
        if (
            stripped
            and len(stripped) >= MIN_DESCRIPTION_LENGTH
            and not stripped.lower().startswith("usage")
            and not stripped.startswith("-")
            and ":" not in stripped[:20]
        ):
            info["description"] = stripped[:200]
            break

    # Extract usage line
    for line in lines:
        lowered = line.lower().strip()
        if lowered.startswith("usage:") or lowered.startswith("usage "):
            info["usage"] = line.strip()[:200]
            break

    # Extract options (lines starting with -)
    option_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("-") and len(stripped) > 2:
            option_lines.append(stripped.split()[0])
            if len(option_lines) >= 10:
                break

    info["options"] = " ".join(option_lines)

    return info


def get_next_command_id(database_file: str = DATABASE_FILE) -> int:
    """
    Find the next available ID in the database.
    """
    max_id = 8000  # Start auto-discovered commands from 8000
    try:
        with open(database_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("C|"):
                    try:
                        current = int(line.split("|")[1])
                        if current >= 8000:
                            max_id = max(max_id, current)
                    except (ValueError, IndexError):
                        continue
    except OSError:
        pass

    return max_id + 1


def command_exists_in_db(
    command: str,
    records: List[Record],
) -> bool:
    """Check if a command is already in the database."""
    command_lower = command.lower()
    for record in records:
        if record["name"].lower() == command_lower:
            return True
    return False


def classify_command(command: str, help_text: str) -> str:
    """
    Guess the category of a command from its name and help.
    """
    text = (command + " " + help_text).lower()

    if any(w in text for w in ("install", "package", "repository")):
        return "package"
    if any(w in text for w in ("network", "http", "url", "socket", "port")):
        return "network"
    if any(w in text for w in ("file", "directory", "path")):
        return "filesystem"
    if any(w in text for w in ("text", "string", "pattern", "regex")):
        return "text"
    if any(w in text for w in ("process", "signal", "kill", "pid")):
        return "process"
    if any(w in text for w in ("git", "version control", "commit")):
        return "development"
    if any(w in text for w in ("video", "audio", "image", "media", "convert")):
        return "media"
    if any(w in text for w in ("database", "sql", "query")):
        return "database"

    return "external"


def auto_add_to_database(
    command: str,
    info: Dict[str, str],
    database_file: str = DATABASE_FILE,
) -> bool:
    """
    Append a new command to the database file.
    """
    if not info.get("description") and not info.get("usage"):
        return False

    next_id = get_next_command_id(database_file)
    command_id = f"{next_id:04d}"

    name = info.get("name", command)
    syntax = info.get("usage", name) or name
    description = info.get("description", "Auto-discovered command")
    category = classify_command(name, description)
    example = f"{name} --help"

    # Sanitize fields (remove pipes and newlines)
    def clean(text: str) -> str:
        return text.replace("|", "/").replace("\n", " ").strip()

    new_line = (
        f"C|{command_id}|{clean(name)}|external|{clean(category)}|"
        f"{clean(syntax)}|{clean(description)}|{clean(example)}|discovered\n"
    )

    try:
        with open(database_file, "a", encoding="utf-8") as f:
            f.write(new_line)
        return True
    except OSError:
        return False

# ============================================================
# PACKAGE INSTALLATION MODE
# ============================================================

# Commands that trigger package installation
INSTALL_TRIGGERS = {
    "pkg":    ["install", "add"],
    "apt":    ["install"],
    "apt-get": ["install"],
    "pip":    ["install"],
    "pip3":   ["install"],
    "npm":    ["install", "i", "add"],
    "yarn":   ["add"],
    "pnpm":   ["add"],
    "gem":    ["install"],
    "cargo":  ["install"],
    "go":     ["install", "get"],
}


def is_install_command(user_input: str) -> Tuple[bool, str, List[str]]:
    """
    Check if the input is a package installation command.
    
    Returns:
        (is_install, manager, packages)
        - is_install: True if it's an install command
        - manager: "pkg" / "apt" / "pip" / ...
        - packages: list of package names to install
    """
    parts = split_input(user_input)
    if not parts or len(parts) < 2:
        return False, "", []

    manager = parts[0].lower()
    action = parts[1].lower()

    if manager not in INSTALL_TRIGGERS:
        return False, "", []

    if action not in INSTALL_TRIGGERS[manager]:
        return False, "", []

    # Extract package names (skip flags)
    packages = []
    for part in parts[2:]:
        if part.startswith("-"):
            continue
        packages.append(part)

    if not packages:
        return False, "", []

    return True, manager, packages


def run_install_command(user_input: str) -> int:
    """
    Run a package installation command in Termux.
    Shows progress to user in English.
    """
    is_install, manager, packages = is_install_command(user_input)

    if not is_install:
        return 1

    print("=" * 50)
    print("  INSTALLING PACKAGES")
    print("=" * 50)
    print()
    print(f"  Package Manager : {manager}")
    print(f"  Packages        : {', '.join(packages)}")
    print()
    print("-" * 50)
    print()

    # Build the actual command
    command_parts = [manager]

    # Add the action (install / add)
    if manager in ("pkg", "apt", "apt-get", "pip", "pip3", "gem"):
        command_parts.append("install")
    elif manager in ("npm",):
        command_parts.append("install")
    elif manager in ("yarn", "pnpm"):
        command_parts.append("add")
    elif manager == "cargo":
        command_parts.append("install")
    elif manager == "go":
        command_parts.append("install")
    else:
        command_parts.append("install")

    # Add packages
    command_parts.extend(packages)

    print(f"Running: {' '.join(command_parts)}")
    print()

    # Run the installation
    try:
        result = subprocess.run(
            command_parts,
            check=False,
        )

        exit_code = result.returncode

    except FileNotFoundError:
        print()
        print(f"[ERROR] Package manager '{manager}' not found.")
        return 1

    except KeyboardInterrupt:
        print()
        print("[INFO] Installation cancelled by user.")
        return 1

    print()
    print("-" * 50)
    print()

    if exit_code == 0:
        print("[OK] Installation completed successfully.")
        print()

        # Auto-learn the installed packages
        print("[AI] Learning installed commands...")
        print()

        learned = 0
        skipped = 0

        for package in packages:
            # Try to learn each package as a command
            import shutil
            if not shutil.which(package):
                # Package name may differ from command name
                # Try common variations
                variations = [
                    package,
                    package.replace("-", ""),
                    package.split("-")[0],
                ]

                found = False
                for variant in variations:
                    if shutil.which(variant):
                        package = variant
                        found = True
                        break

                if not found:
                    skipped += 1
                    continue

            # Check if already in DB
            records = load_command_records()
            if command_exists_in_db(package, records):
                skipped += 1
                continue

            # Read help and add
            info = read_command_help(package)

            if not info.get("description") and not info.get("usage"):
                skipped += 1
                continue

            if auto_add_to_database(package, info):
                learned += 1
                print(f"  [OK] Learned: {package}")

        print()
        if learned > 0:
            print(f"[AI] Added {learned} new command(s) to database.")
        if skipped > 0:
            print(f"[AI] Skipped {skipped} (not found or already known).")

        return 0

    else:
        print(f"[ERROR] Installation failed (exit code: {exit_code}).")
        print()
        print("Possible causes:")
        print("  - Package name is incorrect")
        print("  - Network connection issue")
        print("  - Repository is not updated (try: pkg update)")
        return exit_code

# ============================================================
# PACKAGE INSTALLATION MODE
# ============================================================

# Commands that trigger package installation
INSTALL_TRIGGERS = {
    "pkg":    ["install", "add"],
    "apt":    ["install"],
    "apt-get": ["install"],
    "pip":    ["install"],
    "pip3":   ["install"],
    "npm":    ["install", "i", "add"],
    "yarn":   ["add"],
    "pnpm":   ["add"],
    "gem":    ["install"],
    "cargo":  ["install"],
    "go":     ["install", "get"],
}


def is_install_command(user_input: str) -> Tuple[bool, str, List[str]]:
    """
    Check if the input is a package installation command.
    
    Returns:
        (is_install, manager, packages)
        - is_install: True if it's an install command
        - manager: "pkg" / "apt" / "pip" / ...
        - packages: list of package names to install
    """
    parts = split_input(user_input)
    if not parts or len(parts) < 2:
        return False, "", []

    manager = parts[0].lower()
    action = parts[1].lower()

    if manager not in INSTALL_TRIGGERS:
        return False, "", []

    if action not in INSTALL_TRIGGERS[manager]:
        return False, "", []

    # Extract package names (skip flags)
    packages = []
    for part in parts[2:]:
        if part.startswith("-"):
            continue
        packages.append(part)

    if not packages:
        return False, "", []

    return True, manager, packages


def run_install_command(user_input: str) -> int:
    """
    Run a package installation command in Termux.
    Shows progress to user in English.
    """
    is_install, manager, packages = is_install_command(user_input)

    if not is_install:
        return 1

    print("=" * 50)
    print("  INSTALLING PACKAGES")
    print("=" * 50)
    print()
    print(f"  Package Manager : {manager}")
    print(f"  Packages        : {', '.join(packages)}")
    print()
    print("-" * 50)
    print()

    # Build the actual command
    command_parts = [manager]

    # Add the action (install / add)
    if manager in ("pkg", "apt", "apt-get", "pip", "pip3", "gem"):
        command_parts.append("install")
    elif manager in ("npm",):
        command_parts.append("install")
    elif manager in ("yarn", "pnpm"):
        command_parts.append("add")
    elif manager == "cargo":
        command_parts.append("install")
    elif manager == "go":
        command_parts.append("install")
    else:
        command_parts.append("install")

    # Add packages
    command_parts.extend(packages)

    print(f"Running: {' '.join(command_parts)}")
    print()

    # Run the installation
    try:
        result = subprocess.run(
            command_parts,
            check=False,
        )

        exit_code = result.returncode

    except FileNotFoundError:
        print()
        print(f"[ERROR] Package manager '{manager}' not found.")
        return 1

    except KeyboardInterrupt:
        print()
        print("[INFO] Installation cancelled by user.")
        return 1

    print()
    print("-" * 50)
    print()

    if exit_code == 0:
        print("[OK] Installation completed successfully.")
        print()

        # Auto-learn the installed packages
        print("[AI] Learning installed commands...")
        print()

        learned = 0
        skipped = 0

        for package in packages:
            # Try to learn each package as a command
            import shutil
            if not shutil.which(package):
                # Package name may differ from command name
                # Try common variations
                variations = [
                    package,
                    package.replace("-", ""),
                    package.split("-")[0],
                ]

                found = False
                for variant in variations:
                    if shutil.which(variant):
                        package = variant
                        found = True
                        break

                if not found:
                    skipped += 1
                    continue

            # Check if already in DB
            records = load_command_records()
            if command_exists_in_db(package, records):
                skipped += 1
                continue

            # Read help and add
            info = read_command_help(package)

            if not info.get("description") and not info.get("usage"):
                skipped += 1
                continue

            if auto_add_to_database(package, info):
                learned += 1
                print(f"  [OK] Learned: {package}")

        print()
        if learned > 0:
            print(f"[AI] Added {learned} new command(s) to database.")
        if skipped > 0:
            print(f"[AI] Skipped {skipped} (not found or already known).")

        return 0

    else:
        print(f"[ERROR] Installation failed (exit code: {exit_code}).")
        print()
        print("Possible causes:")
        print("  - Package name is incorrect")
        print("  - Network connection issue")
        print("  - Repository is not updated (try: pkg update)")
        return exit_code

def run_discovery_mode() -> int:
    """
    Main entry for --discover mode.
    Scans system, finds new commands, adds them to database.
    """
    print("DISCOVER MODE")
    print("=" * 32)
    print()

    print("[1/4] Loading database...")
    records = load_command_records()
    known_names = {r["name"].lower() for r in records}
    print(f"      Database has {len(records)} commands.")
    print()

    print("[2/4] Scanning installed commands...")
    installed = discover_installed_commands()
    print(f"      Found {len(installed)} executable commands.")
    print()

    # Filter new commands
    new_commands = [
        cmd for cmd in installed
        if cmd.lower() not in known_names
    ]
    print(f"[3/4] New commands: {len(new_commands)}")
    print()

    if not new_commands:
        print("Nothing to add. Database is up to date.")
        return 0

    print("[4/4] Analyzing new commands...")
    print()

    added = 0
    skipped = 0

    for i, command in enumerate(new_commands, 1):
        print(f"  [{i}/{len(new_commands)}] {command}", end=" ... ")

        info = read_command_help(command)

        if not info.get("description") and not info.get("usage"):
            print("SKIP (no help)")
            skipped += 1
            continue

        if auto_add_to_database(command, info):
            print("ADDED")
            added += 1
        else:
            print("FAILED")
            skipped += 1

    print()
    print("=" * 32)
    print(f"Summary:")
    print(f"  Added:   {added}")
    print(f"  Skipped: {skipped}")
    print(f"  Total:   {added + skipped}")
    print()

    if added > 0:
        print("Restart the app to use the new commands.")

    return 0


def run_learning_mode(command: str) -> int:
    """
    Main entry for --learn mode.
    Learns a specific command and adds it to database.
    """
    if not command:
        print("No command specified.")
        return 1

    print("LEARN MODE")
    print("=" * 32)
    print()

    print(f"Command: {command}")
    print()

    # Check if command exists
    import shutil
    if not shutil.which(command):
        print(f"[ERROR] Command '{command}' is not installed.")
        print("        Install it first with: pkg install " + command)
        return 1

    # Check if already in DB
    records = load_command_records()
    if command_exists_in_db(command, records):
        print(f"[INFO] Command '{command}' is already in the database.")
        return 0

    print("[1/2] Reading --help...")
    info = read_command_help(command)

    if not info.get("description") and not info.get("usage"):
        print("      No useful help information found.")
        print("      Cannot learn this command.")
        return 1

    print(f"      Description: {info.get('description', 'N/A')[:80]}")
    print(f"      Usage:       {info.get('usage', 'N/A')[:80]}")
    print()

    print("[2/2] Adding to database...")
    if auto_add_to_database(command, info):
        print("      Added successfully!")
        print()
        print("Restart the app to use this command.")
        return 0
    else:
        print("      Failed to add.")
        return 1

# ============================================================
# MAIN
# ============================================================

def print_suggestions(
    suggestions: List[Tuple[float, str, Record]],
    user_input: str,
) -> None:
    """Pretty-print suggestions."""
    if not suggestions:
        print("No close correction found.")
        return

    print(f"Found {len(suggestions)} suggestion(s):\n")

    for i, (score, corrected, record) in enumerate(suggestions, 1):
        explanation = explain_suggestion(score, record, user_input)
        print(f"  {i}. {corrected}")
        print(f"     score: {score:.2f}  {explanation}")
        print(f"     {record.get('description', '')}")
        print()


def main() -> int:
    print("================================")
    print("      ADVANCED AI v4.0")
    print("================================")
    print()

    print("Loading database...")
    records = load_command_records()

    if not records:
        print()
        print("AI initialization failed.")
        return 1

    print(f"Loaded {len(records)} commands.")

    # Load learning store
    learning = LearningStore()

    print("AI is ready.")
    print()

    if len(sys.argv) <= 1:
        return 0
    
    # Handle special modes
    first_arg = sys.argv[1]
    
    if first_arg == "--discover":
        return run_discovery_mode()
    
    if first_arg == "--learn":
        if len(sys.argv) < 3:
            print("Usage: advanced_ai.py --learn COMMAND")
            return 1
        return run_learning_mode(sys.argv[2])
        
    if first_arg == "--install":
        user_command = " ".join(sys.argv[2:]).strip()
        if not user_command:
            print("Usage: advanced_ai.py --install COMMAND")
            return 1
        return run_install_command(user_command)
    
    # Normal correction mode
    user_input = " ".join(sys.argv[1:]).strip()
    
    if not user_input:
        print("No command supplied.")
        return 0
    
    # Apply preprocessing
    original_input = user_input
    user_input = fix_common_typos(user_input)
    user_input = expand_abbreviations(user_input)

    if user_input != original_input:
        print(f"Preprocessed: {user_input}")
        print()

    # Check known corrections first
    known = learning.get_known_correction(original_input)
    if known:
        print("Known correction:")
        print(known)
        print()
        return 0

    # Generate suggestions
    suggestions = suggest_commands(user_input, records, learning)

    print_suggestions(suggestions, user_input)

    # Record the query (for learning)
    if suggestions:
        parts = split_input(user_input)
        if parts:
            learning.record_correction(parts[0], suggestions[0][1])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
