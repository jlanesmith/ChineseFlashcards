#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chinese Flashcard App - Practice your vocabulary!"""

import csv
import random
import os
import sys
import base64
import io
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# For cross-platform key detection
try:
    import tty
    import termios
    UNIX = True
except ImportError:
    import msvcrt
    UNIX = False

# Debug logging
DEBUG_LOG = True
DEBUG_LOG_PATH = Path(__file__).parent / 'debug.log'

def debug_log(msg):
    """Write debug message to log file."""
    if DEBUG_LOG:
        with open(DEBUG_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - {msg}\n")

# Check for image support
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def flush_stdin():
    """Flush any pending input from stdin."""
    debug_log("flush_stdin() called")
    if UNIX:
        import select
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            # Keep reading until nothing is pending
            flushed = []
            while select.select([sys.stdin], [], [], 0.1)[0]:
                ch = sys.stdin.read(1)
                flushed.append(f"ord={ord(ch)} repr={repr(ch)}")
            if flushed:
                debug_log(f"flush_stdin() flushed: {flushed}")
            else:
                debug_log("flush_stdin() nothing to flush")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def get_key():
    """Get a single keypress, filtering out escape sequences from mouse events."""
    if UNIX:
        import select
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            debug_log(f"get_key() read: ord={ord(ch)} repr={repr(ch)}")

            # If it's an escape character, consume the rest of the escape sequence
            if ch == '\x1b':
                consumed = []
                # Consume all pending input from the escape sequence
                # Use longer timeout and multiple passes to ensure we get everything
                for _ in range(3):  # Multiple passes to catch delayed characters
                    while select.select([sys.stdin], [], [], 0.15)[0]:
                        esc_ch = sys.stdin.read(1)
                        consumed.append(f"ord={ord(esc_ch)} repr={repr(esc_ch)}")
                debug_log(f"get_key() escape sequence consumed: {consumed}")
                return None  # Return None for escape sequences

            # Filter out all control characters (ord < 32) except we handle space separately
            # This catches any stray bytes from mouse events
            if ord(ch) < 32 and ch != ' ':
                consumed = []
                # Consume any following characters that might be part of a sequence
                while select.select([sys.stdin], [], [], 0.05)[0]:
                    ctrl_ch = sys.stdin.read(1)
                    consumed.append(f"ord={ord(ctrl_ch)} repr={repr(ctrl_ch)}")
                debug_log(f"get_key() control char filtered, consumed: {consumed}")
                return None

            # Return allowed characters
            if ch == ' ' or ch in 'qQxX0123456789pdwmsPDWMSuUiItTbBhH':
                debug_log(f"get_key() returning: {repr(ch)}")
                return ch

            # Ignore other characters (could be high bytes from mouse events)
            debug_log(f"get_key() ignoring: ord={ord(ch)} repr={repr(ch)}")
            return None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    else:
        ch = msvcrt.getch().decode('utf-8', errors='ignore')
        debug_log(f"get_key() Windows read: {repr(ch)}")
        if ch == ' ' or ch in 'qQxX0123456789pdwmsPDWMSuUiItTbBhH':
            return ch
        return None


def check_for_quit(key):
    """Check if user is typing 'quit'. Returns True if quit detected, False otherwise."""
    if key is None:
        return False
    if key.lower() != 'q':
        return False

    # User typed 'q', now check for 'uit'
    debug_log("check_for_quit() - got 'q', checking for 'uit'")
    buffer = 'q'

    # Wait briefly for the remaining characters with timeout
    import time
    timeout = time.time() + 1.0  # 1 second to type "quit"

    while len(buffer) < 4 and time.time() < timeout:
        key = get_key()
        if key is None:
            continue
        buffer += key.lower()
        debug_log(f"check_for_quit() - buffer now: {buffer}")

        if buffer == 'quit':
            debug_log("check_for_quit() - QUIT confirmed")
            return True
        elif not 'quit'.startswith(buffer):
            # User typed something that's not part of 'quit'
            debug_log(f"check_for_quit() - not quit, buffer: {buffer}")
            return False

    # Timeout or wrong sequence
    debug_log(f"check_for_quit() - timeout or incomplete, buffer: {buffer}")
    return False


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def load_words(csv_path):
    """Load words from CSV file."""
    words = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            words.append({
                'pinyin': row['pinyin'],
                'meaning': row['meaning'],
                'tone': row['tone'],
                'group': int(row['group']),
                'character': row['character']
            })
    return words


def get_groups(words):
    """Get unique group numbers."""
    return sorted(set(w['group'] for w in words))


def load_results(results_path):
    """Load results from CSV file."""
    results = []
    if not results_path.exists():
        return results
    with open(results_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                results.append({
                    'timestamp': datetime.fromisoformat(row['timestamp']),
                    'session_id': row['session_id'],
                    'pinyin': row['pinyin'],
                    'character': row['character'],
                    'group': int(row['group']),
                    'correct': row['correct'] == 'yes'
                })
            except (ValueError, KeyError):
                continue
    return results


def get_mistake_words(words, results, time_period):
    """Get words that had mistakes within the time period."""
    now = datetime.now()

    if time_period == 'day':
        cutoff = now - timedelta(days=1)
    elif time_period == 'week':
        cutoff = now - timedelta(weeks=1)
    elif time_period == 'month':
        cutoff = now - timedelta(days=30)
    else:
        cutoff = datetime.min

    # Find characters with mistakes in the period
    mistake_chars = set()
    for r in results:
        if r['timestamp'] >= cutoff and not r['correct']:
            mistake_chars.add(r['character'])

    # Filter words
    return [w for w in words if w['character'] in mistake_chars]


def save_result(results_path, word, correct, session_id, selected_groups):
    """Save a test result to CSV."""
    file_exists = results_path.exists()

    with open(results_path, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['timestamp', 'session_id', 'pinyin', 'character', 'group', 'correct'])
        writer.writerow([
            datetime.now().isoformat(),
            session_id,
            word['pinyin'],
            word['character'],
            word['group'],
            'yes' if correct else 'no'
        ])


def save_practice_time(results_path, session_id, duration_seconds, selected_groups):
    """Save practice session time (without recording individual results)."""
    # Encode selected groups in the group field
    # For single group or all groups (0): store directly
    # For multiple groups: store as comma-separated in pinyin field
    file_exists = results_path.exists()

    if not selected_groups or 0 in selected_groups:
        # All groups
        group_code = 0
        pinyin_start = '_practice_start_'
        pinyin_end = '_practice_end_'
    elif len(selected_groups) == 1:
        # Single group
        group_code = list(selected_groups)[0]
        pinyin_start = '_practice_start_'
        pinyin_end = '_practice_end_'
    else:
        # Multiple groups - encode in pinyin
        groups_str = ','.join(map(str, sorted(selected_groups)))
        group_code = -1  # Marker for "multiple groups"
        pinyin_start = f'_practice_start_{groups_str}_'
        pinyin_end = f'_practice_end_{groups_str}_'

    with open(results_path, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['timestamp', 'session_id', 'pinyin', 'character', 'group', 'correct'])
        # Write start marker (session start time = now - duration)
        writer.writerow([
            (datetime.now() - timedelta(seconds=duration_seconds)).isoformat(),
            f"practice_{session_id}",
            pinyin_start,
            '_practice_',
            group_code,
            'practice'
        ])
        # Write end marker (session end time = now)
        writer.writerow([
            datetime.now().isoformat(),
            f"practice_{session_id}",
            pinyin_end,
            '_practice_',
            group_code,
            'practice'
        ])


def render_large_character(char):
    """Render Chinese character in large format using block characters."""
    # Create a visually striking large display
    lines = []
    lines.append("┏" + "━" * 30 + "┓")
    lines.append("┃" + " " * 30 + "┃")
    lines.append("┃" + " " * 30 + "┃")

    # Center the character - Chinese chars are ~2 columns wide
    char_display = f"  {char}  "
    # Calculate padding for centering
    total_width = 30
    char_visual_width = len(char) * 2 + 4  # rough estimate
    left_pad = (total_width - char_visual_width) // 2
    right_pad = total_width - left_pad - char_visual_width

    lines.append("┃" + " " * 30 + "┃")
    lines.append("┃" + " " * 30 + "┃")
    lines.append("┗" + "━" * 30 + "┛")

    return lines


def is_iterm2():
    """Check if running in iTerm2."""
    return os.environ.get('TERM_PROGRAM') == 'iTerm.app'


def is_kitty():
    """Check if running in Kitty terminal."""
    return os.environ.get('TERM') == 'xterm-kitty'


def display_image_iterm2(image_data):
    """Display image inline using iTerm2's escape sequence."""
    b64_data = base64.b64encode(image_data).decode('ascii')
    # iTerm2 inline image protocol
    sys.stdout.write(f'\033]1337;File=inline=1;width=40;height=12;preserveAspectRatio=1:{b64_data}\a')
    sys.stdout.flush()


def display_image_kitty(image_data):
    """Display image inline using Kitty's escape sequence."""
    b64_data = base64.b64encode(image_data).decode('ascii')
    # Kitty graphics protocol - chunked transmission
    chunk_size = 4096
    chunks = [b64_data[i:i+chunk_size] for i in range(0, len(b64_data), chunk_size)]

    for i, chunk in enumerate(chunks):
        m = 1 if i < len(chunks) - 1 else 0  # more chunks coming?
        if i == 0:
            sys.stdout.write(f'\033_Ga=T,f=100,m={m};{chunk}\033\\')
        else:
            sys.stdout.write(f'\033_Gm={m};{chunk}\033\\')
    sys.stdout.flush()


def create_character_image(char, size=300):
    """Create an image of the Chinese character."""
    if not HAS_PIL:
        return None

    # Try to find a good font for Chinese characters
    font = None
    font_paths = [
        # macOS fonts
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/Library/Fonts/Arial Unicode.ttf',
        # Linux fonts
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
        # Windows fonts
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/simsun.ttc',
    ]

    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, size)
                break
            except:
                continue

    if font is None:
        # Fall back to default font (won't look as good for Chinese)
        try:
            font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', size)
        except:
            font = ImageFont.load_default()

    # Measure text size first to determine image dimensions
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    bbox = temp_draw.textbbox((0, 0), char, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Create image sized to fit the text with padding
    padding = 40
    img_width = text_width + padding * 2
    img_height = text_height + padding * 2
    img = Image.new('RGB', (img_width, img_height), color=(15, 52, 96))  # Dark blue background

    draw = ImageDraw.Draw(img)

    x = (img_width - text_width) // 2 - bbox[0]
    y = (img_height - text_height) // 2 - bbox[1]

    # Draw character in bright green
    draw.text((x, y), char, font=font, fill=(76, 175, 80))  # Green color

    # Add a border
    draw.rectangle([2, 2, img_width-3, img_height-3], outline=(233, 69, 96), width=3)

    # Save to bytes
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return buffer.getvalue()


def display_huge_character(char):
    """Display character in HUGE format - as image if possible, otherwise text."""
    BOLD = '\033[1m'
    GREEN = '\033[92m'
    RESET = '\033[0m'

    # Try to display as image
    if HAS_PIL and (is_iterm2() or is_kitty()):
        image_data = create_character_image(char, size=280)
        if image_data:
            print()
            print()
            if is_iterm2():
                # Add padding for centering
                print("      ", end='')
                display_image_iterm2(image_data)
            elif is_kitty():
                display_image_kitty(image_data)
            print()
            print()
            return

    # Fallback to text-based display
    # Calculate width needed - each Chinese character is ~2 columns wide
    num_chars = len(char)
    char_visual_width = num_chars * 2  # Chinese chars are 2 columns wide
    min_width = char_visual_width + 20  # Padding around the character
    width = max(52, min_width)

    print()
    print(f"  {'█' * width}")
    print(f"  █{' ' * (width-2)}█")
    print(f"  █{' ' * (width-2)}█")
    print(f"  █{' ' * (width-2)}█")

    # Calculate centering - account for Chinese chars being 2 columns wide
    display_char = f"   {char}   "
    display_visual_width = 6 + char_visual_width  # 6 spaces + character width
    inner_width = width - 2
    left = (inner_width - display_visual_width) // 2
    right = inner_width - left - display_visual_width

    print(f"  █{' ' * left}{BOLD}{GREEN}{display_char}{RESET}{' ' * right}█")

    print(f"  █{' ' * (width-2)}█")
    print(f"  █{' ' * (width-2)}█")
    print(f"  █{' ' * (width-2)}█")
    print(f"  {'█' * width}")
    print()


def display_menu(groups, has_results):
    """Display group selection menu."""
    clear_screen()
    W = 50  # inner width

    def char_width(c):
        """Get display width of a character (Chinese chars are 2 wide)."""
        code = ord(c)
        # CJK characters, emojis, and other wide characters
        if (0x4E00 <= code <= 0x9FFF or      # CJK Unified Ideographs
            0x3400 <= code <= 0x4DBF or      # CJK Extension A
            0x1F000 <= code <= 0x1FFFF or    # Emojis
            0x2600 <= code <= 0x26FF or      # Misc symbols
            0x2700 <= code <= 0x27BF):       # Dingbats
            return 2
        return 1

    def visible_width(text):
        """Calculate visible width of text."""
        return sum(char_width(c) for c in text)

    def pad(text, width=W):
        """Pad text to width, accounting for wide characters."""
        vis_width = visible_width(text)
        padding = width - vis_width
        return text + ' ' * max(0, padding)

    print(f"╔{'═' * W}╗")
    print(f"║{pad('       中文 CHINESE FLASHCARDS 中文')}║")
    print(f"╠{'═' * W}╣")
    print(f"║{' ' * W}║")
    print(f"║{pad('  📚 TEST BY GROUP (random order, recorded):')}║")

    for g in groups:
        line = f"      [{g:2d}] Group {g}"
        print(f"║{pad(line)}║")

    print(f"║{pad('      [ 0] All groups')}║")
    print(f"║{' ' * W}║")

    print(f"║{pad('  📖 PRACTICE (in order, not recorded):')}║")
    print(f"║{pad('      [ p] Practice all groups')}║")
    print(f"║{pad('      [pN] Practice group N (e.g., p3)')}║")
    pd_text = "      [pd] Practice today's mistakes"
    pw_text = "      [pw] Practice this week's mistakes"
    pm_text = "      [pm] Practice this month's mistakes"
    print(f"║{pad(pd_text)}║")
    print(f"║{pad(pw_text)}║")
    print(f"║{pad(pm_text)}║")
    print(f"║{' ' * W}║")

    if has_results:
        print(f"║{pad('  🔄 REVIEW MISTAKES (random, recorded):')}║")
        print(f"║{pad('      [ d] Mistakes from today')}║")
        print(f"║{pad('      [ w] Mistakes from this week')}║")
        print(f"║{pad('      [ m] Mistakes from this month')}║")
        print(f"║{' ' * W}║")

    print(f"║{pad('  📊 STATISTICS:')}║")
    print(f"║{pad('      [ s] View stats & charts')}║")
    print(f"║{' ' * W}║")
    print(f"║{pad('  📜 HISTORY:')}║")
    print(f"║{pad('      [ h] View session history')}║")
    print(f"║{' ' * W}║")
    print(f"║{pad('      [quit] Quit')}║")
    print(f"║{' ' * W}║")
    print(f"╚{'═' * W}╝")
    print()
    return input("Enter choice: ").strip().lower()


def display_card(word, show_answer, correct_count, incorrect_count, remaining, practice_mode=False, current_num=0, total_num=0):
    """Display a flashcard."""
    clear_screen()

    GREEN = '\033[92m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

    # Status line
    if practice_mode:
        print(f"  {CYAN}📖 PRACTICE MODE{RESET}  │  Card {current_num} of {total_num}")
    else:
        print(f"  {GREEN}✓ {correct_count}{RESET}  {RED}✗ {incorrect_count}{RESET}  │  Remaining: {remaining}")
    print("  " + "─" * 50)
    print()

    # Card display
    print("  ╔════════════════════════════════════════════════╗")
    print("  ║                                                ║")
    print(f"  ║  {BOLD}Pinyin:{RESET}  {word['pinyin']:<36} ║")
    print("  ║                                                ║")

    # Handle long meanings
    meaning = word['meaning']
    if len(meaning) > 36:
        meaning = meaning[:33] + "..."
    print(f"  ║  {BOLD}Meaning:{RESET} {meaning:<36} ║")
    print("  ║                                                ║")
    print(f"  ║  {BOLD}Group:{RESET}   {word['group']:<36} ║")
    print("  ║                                                ║")
    print("  ╚════════════════════════════════════════════════╝")

    if show_answer:
        display_huge_character(word['character'])
        if practice_mode:
            if current_num > 1:
                print(f"  {GREEN}[SPACE]{RESET} Next   {CYAN}[B]{RESET} Back   [quit] Quit")
            else:
                print(f"  {GREEN}[SPACE]{RESET} Next   [quit] Quit")
        else:
            print(f"  {GREEN}[SPACE]{RESET} Correct   {RED}[X]{RESET} Incorrect   [quit] Quit")
    else:
        print()
        print("  ┌────────────────────────────────────────────────┐")
        print("  │                                                │")
        print("  │                      ???                       │")
        print("  │                                                │")
        print("  └────────────────────────────────────────────────┘")
        print()
        print("  [SPACE] Reveal answer")


def calculate_stats(results, words):
    """Calculate statistics from results."""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(weeks=1)

    stats = {
        'total': {
            'tested': 0,
            'correct': 0,
            'incorrect': 0,
            'unique_chars': set(),
            'sessions': set()
        },
        'week': {
            'tested': 0,
            'correct': 0,
            'incorrect': 0,
            'unique_chars': set(),
            'sessions': set()
        },
        'today': {
            'tested': 0,
            'correct': 0,
            'incorrect': 0,
            'unique_chars': set(),
            'sessions': set()
        },
        'by_character': defaultdict(lambda: {'correct': 0, 'incorrect': 0, 'last_seen': None}),
        'by_date': defaultdict(lambda: {'correct': 0, 'incorrect': 0}),
        'by_session': defaultdict(lambda: {'start': None, 'end': None, 'count': 0}),
        'all_chars': set(w['character'] for w in words)
    }

    for r in results:
        char = r['character']
        date_key = r['timestamp'].strftime('%Y-%m-%d')
        session_id = r['session_id']

        # Handle practice markers for time tracking only
        if char == '_practice_':
            # Track session timing for practice sessions
            if stats['by_session'][session_id]['start'] is None or r['timestamp'] < stats['by_session'][session_id]['start']:
                stats['by_session'][session_id]['start'] = r['timestamp']
            if stats['by_session'][session_id]['end'] is None or r['timestamp'] > stats['by_session'][session_id]['end']:
                stats['by_session'][session_id]['end'] = r['timestamp']

            # Add to session sets for time calculations
            stats['total']['sessions'].add(session_id)
            if r['timestamp'] >= week_ago:
                stats['week']['sessions'].add(session_id)
            if r['timestamp'] >= today_start:
                stats['today']['sessions'].add(session_id)

            continue  # Skip the rest of stats for practice markers

        # Regular test result stats
        # Total stats
        stats['total']['tested'] += 1
        stats['total']['unique_chars'].add(char)
        stats['total']['sessions'].add(session_id)
        if r['correct']:
            stats['total']['correct'] += 1
        else:
            stats['total']['incorrect'] += 1

        # Weekly stats
        if r['timestamp'] >= week_ago:
            stats['week']['tested'] += 1
            stats['week']['unique_chars'].add(char)
            stats['week']['sessions'].add(session_id)
            if r['correct']:
                stats['week']['correct'] += 1
            else:
                stats['week']['incorrect'] += 1

        # Today stats
        if r['timestamp'] >= today_start:
            stats['today']['tested'] += 1
            stats['today']['unique_chars'].add(char)
            stats['today']['sessions'].add(session_id)
            if r['correct']:
                stats['today']['correct'] += 1
            else:
                stats['today']['incorrect'] += 1

        # Per-character stats
        stats['by_character'][char]['correct' if r['correct'] else 'incorrect'] += 1
        if stats['by_character'][char]['last_seen'] is None or r['timestamp'] > stats['by_character'][char]['last_seen']:
            stats['by_character'][char]['last_seen'] = r['timestamp']

        # Per-date stats
        stats['by_date'][date_key]['correct' if r['correct'] else 'incorrect'] += 1

        # Per-session stats (for time tracking)
        if stats['by_session'][session_id]['start'] is None or r['timestamp'] < stats['by_session'][session_id]['start']:
            stats['by_session'][session_id]['start'] = r['timestamp']
        if stats['by_session'][session_id]['end'] is None or r['timestamp'] > stats['by_session'][session_id]['end']:
            stats['by_session'][session_id]['end'] = r['timestamp']
        stats['by_session'][session_id]['count'] += 1

    # Calculate new characters (in words but never tested)
    stats['new_chars'] = stats['all_chars'] - stats['total']['unique_chars']

    # Calculate time spent
    def calc_time(session_ids):
        total_seconds = 0
        for sid in session_ids:
            session = stats['by_session'].get(sid)
            if session and session['start'] and session['end']:
                duration = (session['end'] - session['start']).total_seconds()
                # Add estimated time for the last card (avg ~5 seconds)
                duration += 5
                total_seconds += duration
        return total_seconds

    stats['time'] = {
        'total': calc_time(stats['total']['sessions']),
        'week': calc_time(stats['week']['sessions']),
        'today': calc_time(stats['today']['sessions'])
    }

    return stats


def format_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def display_stats(results, words, results_path):
    """Display statistics and generate charts."""
    clear_screen()

    if not results:
        print("\n  No results yet! Start practicing to see stats.\n")
        input("  Press Enter to continue...")
        return

    stats = calculate_stats(results, words)

    BOLD = '\033[1m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'

    # Box width (inner content width)
    W = 62

    def pad(text, width):
        """Pad text to width, accounting for ANSI codes."""
        import re
        visible = re.sub(r'\033\[[0-9;]*m', '', text)
        padding = width - len(visible)
        return text + ' ' * max(0, padding)

    # Extract values for cleaner code
    today = stats['today']
    week = stats['week']
    total = stats['total']
    time_today = format_duration(stats['time']['today'])
    time_week = format_duration(stats['time']['week'])
    time_total = format_duration(stats['time']['total'])

    print()
    print(f"  {BOLD}╔{'═' * W}╗{RESET}")
    print(f"  {BOLD}║{RESET}{pad('                      📊 STATISTICS 📊', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}╠{'═' * W}╣{RESET}")
    print(f"  {BOLD}║{RESET}{' ' * W}{BOLD}║{RESET}")

    # Today stats
    print(f"  {BOLD}║{RESET}  {MAGENTA}TODAY:{RESET}{' ' * (W - 8)}{BOLD}║{RESET}")
    sessions_today = len(today['sessions'])
    line = f"    Sessions: {sessions_today:<10} Time: {time_today}"
    print(f"  {BOLD}║{RESET}{pad(line, W)}{BOLD}║{RESET}")
    tested_today = today['tested']
    correct_today = today['correct']
    incorrect_today = today['incorrect']
    print(f"  {BOLD}║{RESET}{pad(f'    Total tested: {tested_today}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {GREEN}Correct:{RESET} {correct_today}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {RED}Incorrect:{RESET} {incorrect_today}', W)}{BOLD}║{RESET}")
    if tested_today > 0:
        acc = correct_today / tested_today * 100
        print(f"  {BOLD}║{RESET}{pad(f'    Accuracy: {acc:.1f}%', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{' ' * W}{BOLD}║{RESET}")

    # This week stats
    print(f"  {BOLD}║{RESET}  {CYAN}THIS WEEK:{RESET}{' ' * (W - 12)}{BOLD}║{RESET}")
    sessions_week = len(week['sessions'])
    line = f"    Sessions: {sessions_week:<10} Time: {time_week}"
    print(f"  {BOLD}║{RESET}{pad(line, W)}{BOLD}║{RESET}")
    tested_week = week['tested']
    correct_week = week['correct']
    incorrect_week = week['incorrect']
    print(f"  {BOLD}║{RESET}{pad(f'    Total tested: {tested_week}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {GREEN}Correct:{RESET} {correct_week}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {RED}Incorrect:{RESET} {incorrect_week}', W)}{BOLD}║{RESET}")
    if tested_week > 0:
        acc = correct_week / tested_week * 100
        print(f"  {BOLD}║{RESET}{pad(f'    Accuracy: {acc:.1f}%', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{' ' * W}{BOLD}║{RESET}")

    # All time stats
    print(f"  {BOLD}║{RESET}  {YELLOW}ALL TIME:{RESET}{' ' * (W - 11)}{BOLD}║{RESET}")
    sessions_total = len(total['sessions'])
    line = f"    Sessions: {sessions_total:<10} Time: {time_total}"
    print(f"  {BOLD}║{RESET}{pad(line, W)}{BOLD}║{RESET}")
    tested_total = total['tested']
    correct_total = total['correct']
    incorrect_total = total['incorrect']
    unique_chars = len(total['unique_chars'])
    new_chars = len(stats['new_chars'])
    print(f"  {BOLD}║{RESET}{pad(f'    Total tested: {tested_total}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {GREEN}Correct:{RESET} {correct_total}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {RED}Incorrect:{RESET} {incorrect_total}', W)}{BOLD}║{RESET}")

    if tested_total > 0:
        acc = correct_total / tested_total * 100
        print(f"  {BOLD}║{RESET}{pad(f'    Accuracy: {acc:.1f}%', W)}{BOLD}║{RESET}")

    print(f"  {BOLD}║{RESET}{pad(f'    Unique chars: {unique_chars}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{pad(f'    {YELLOW}New (untested):{RESET} {new_chars}', W)}{BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}{' ' * W}{BOLD}║{RESET}")
    print(f"  {BOLD}╚{'═' * W}╝{RESET}")
    print()

    # Show worst performing characters (filter out practice markers)
    char_stats = [(char, data) for char, data in stats['by_character'].items() if char != '_practice_']
    char_stats.sort(key=lambda x: x[1]['incorrect'] / max(1, x[1]['correct'] + x[1]['incorrect']), reverse=True)

    print(f"  {BOLD}CHARACTERS NEEDING WORK (highest error rate):{RESET}")
    print(f"  {'─' * 50}")

    shown = 0
    for char, data in char_stats[:10]:
        total = data['correct'] + data['incorrect']
        if total > 0 and data['incorrect'] > 0:
            error_rate = data['incorrect'] / total * 100
            # Find pinyin for this character
            pinyin = next((w['pinyin'] for w in words if w['character'] == char), '?')
            print(f"    {char} ({pinyin}): {RED}{data['incorrect']}{RESET}/{total} = {error_rate:.0f}% errors")
            shown += 1

    if shown == 0:
        print(f"    {GREEN}No mistakes yet! Keep up the great work!{RESET}")

    print()

    # Show best performing characters
    char_stats.sort(key=lambda x: x[1]['correct'] / max(1, x[1]['correct'] + x[1]['incorrect']), reverse=True)

    print(f"  {BOLD}MASTERED CHARACTERS (highest accuracy, 5+ attempts):{RESET}")
    print(f"  {'─' * 50}")

    shown = 0
    for char, data in char_stats:
        total = data['correct'] + data['incorrect']
        if total >= 5 and data['incorrect'] == 0:
            pinyin = next((w['pinyin'] for w in words if w['character'] == char), '?')
            print(f"    {GREEN}★{RESET} {char} ({pinyin}): {total}/{total} = 100%")
            shown += 1
            if shown >= 10:
                break

    if shown == 0:
        print(f"    Keep practicing to master characters!")

    print()
    print("  [p] Generate performance plots")
    print("  [Enter] Return to menu")

    choice = input("\n  Choice: ").strip().lower()

    if choice == 'p':
        generate_plots(stats, words, results_path.parent)


def generate_plots(stats, words, output_dir):
    """Generate performance plots."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
    except ImportError:
        print("\n  matplotlib not installed. Install with: pip install matplotlib")
        input("  Press Enter to continue...")
        return

    GREEN = '\033[92m'
    RESET = '\033[0m'

    print("\n  Generating plots...")

    # Create figure with multiple subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Chinese Flashcard Performance', fontsize=16, fontweight='bold')

    # 1. Daily performance over time
    ax1 = axes[0, 0]
    dates = sorted(stats['by_date'].keys())[-30:]  # Last 30 days
    if dates:
        correct_vals = [stats['by_date'][d]['correct'] for d in dates]
        incorrect_vals = [stats['by_date'][d]['incorrect'] for d in dates]

        x = range(len(dates))
        ax1.bar(x, correct_vals, label='Correct', color='#4CAF50', alpha=0.8)
        ax1.bar(x, incorrect_vals, bottom=correct_vals, label='Incorrect', color='#f44336', alpha=0.8)
        ax1.set_xlabel('Date')
        ax1.set_ylabel('Count')
        ax1.set_title('Daily Performance (Last 30 Days)')
        ax1.legend()

        # Show fewer x-tick labels
        tick_positions = list(range(0, len(dates), max(1, len(dates) // 7)))
        ax1.set_xticks(tick_positions)
        ax1.set_xticklabels([dates[i][5:] for i in tick_positions], rotation=45)
    else:
        ax1.text(0.5, 0.5, 'No data yet', ha='center', va='center', transform=ax1.transAxes)
        ax1.set_title('Daily Performance')

    # 2. Character difficulty (error rate)
    ax2 = axes[0, 1]
    char_data = [(char, data) for char, data in stats['by_character'].items()]
    char_data = [(c, d) for c, d in char_data if d['correct'] + d['incorrect'] >= 3]  # Min 3 attempts
    char_data.sort(key=lambda x: x[1]['incorrect'] / max(1, x[1]['correct'] + x[1]['incorrect']), reverse=True)

    if char_data:
        top_difficult = char_data[:15]
        chars = [c for c, _ in top_difficult]
        error_rates = [d['incorrect'] / (d['correct'] + d['incorrect']) * 100 for _, d in top_difficult]

        colors = ['#f44336' if r > 50 else '#FF9800' if r > 25 else '#4CAF50' for r in error_rates]
        bars = ax2.barh(range(len(chars)), error_rates, color=colors)
        ax2.set_yticks(range(len(chars)))
        ax2.set_yticklabels(chars, fontsize=12)
        ax2.set_xlabel('Error Rate (%)')
        ax2.set_title('Most Difficult Characters')
        ax2.invert_yaxis()
        ax2.set_xlim(0, 100)
    else:
        ax2.text(0.5, 0.5, 'Need more attempts\n(min 3 per character)', ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title('Most Difficult Characters')

    # 3. Accuracy by group
    ax3 = axes[1, 0]
    group_stats = defaultdict(lambda: {'correct': 0, 'incorrect': 0})
    for char, data in stats['by_character'].items():
        # Find group for this character
        word = next((w for w in words if w['character'] == char), None)
        if word:
            group_stats[word['group']]['correct'] += data['correct']
            group_stats[word['group']]['incorrect'] += data['incorrect']

    if group_stats:
        groups = sorted(group_stats.keys())
        accuracies = []
        for g in groups:
            total = group_stats[g]['correct'] + group_stats[g]['incorrect']
            acc = group_stats[g]['correct'] / total * 100 if total > 0 else 0
            accuracies.append(acc)

        colors = ['#4CAF50' if a >= 80 else '#FF9800' if a >= 60 else '#f44336' for a in accuracies]
        ax3.bar([f'G{g}' for g in groups], accuracies, color=colors)
        ax3.set_xlabel('Group')
        ax3.set_ylabel('Accuracy (%)')
        ax3.set_title('Accuracy by Group')
        ax3.set_ylim(0, 100)
        ax3.axhline(y=80, color='green', linestyle='--', alpha=0.5, label='Target (80%)')
        ax3.legend()
    else:
        ax3.text(0.5, 0.5, 'No data yet', ha='center', va='center', transform=ax3.transAxes)
        ax3.set_title('Accuracy by Group')

    # 4. Overall progress pie chart
    ax4 = axes[1, 1]
    total_chars = len(stats['all_chars'])
    tested_chars = len(stats['total']['unique_chars'])
    untested_chars = len(stats['new_chars'])

    # Calculate mastered (>80% accuracy with 5+ attempts)
    mastered = 0
    learning = 0
    struggling = 0
    for char, data in stats['by_character'].items():
        total = data['correct'] + data['incorrect']
        if total >= 5:
            acc = data['correct'] / total
            if acc >= 0.8:
                mastered += 1
            elif acc >= 0.5:
                learning += 1
            else:
                struggling += 1
        elif total > 0:
            learning += 1

    sizes = [mastered, learning, struggling, untested_chars]
    labels = [f'Mastered\n({mastered})', f'Learning\n({learning})', f'Struggling\n({struggling})', f'Untested\n({untested_chars})']
    colors = ['#4CAF50', '#2196F3', '#f44336', '#9E9E9E']
    explode = (0.05, 0, 0.05, 0)

    # Remove zero-sized slices
    non_zero = [(s, l, c, e) for s, l, c, e in zip(sizes, labels, colors, explode) if s > 0]
    if non_zero:
        sizes, labels, colors, explode = zip(*non_zero)
        ax4.pie(sizes, labels=labels, colors=colors, explode=explode, autopct='%1.0f%%', startangle=90)
        ax4.set_title(f'Character Mastery\n({total_chars} total characters)')
    else:
        ax4.text(0.5, 0.5, 'No data yet', ha='center', va='center', transform=ax4.transAxes)
        ax4.set_title('Character Mastery')

    plt.tight_layout()

    # Save the plot
    plot_path = output_dir / 'performance_chart.png'
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"\n  {GREEN}✓{RESET} Charts saved to: {plot_path}")

    # Try to open the image
    try:
        if sys.platform == 'darwin':  # macOS
            os.system(f'open "{plot_path}"')
        elif sys.platform == 'win32':  # Windows
            os.startfile(str(plot_path))
        else:  # Linux
            os.system(f'xdg-open "{plot_path}"')
    except:
        pass

    input("\n  Press Enter to continue...")


def run_practice(words, selected_groups, results_path, mistake_mode=None, all_results=None):
    """Run practice mode - in order, no recording of correct/incorrect."""
    debug_log("="*50)
    debug_log("run_practice() STARTED")

    # Flush any pending input (e.g., from mouse events)
    flush_stdin()

    # Filter words by selected groups or mistakes
    if mistake_mode and all_results:
        practice_words = get_mistake_words(words, all_results, mistake_mode)
        if not practice_words:
            clear_screen()
            print(f"\n  No mistakes found for the selected time period!")
            print("  Great job - or try testing first!\n")
            input("  Press Enter to continue...")
            return
    elif 0 in selected_groups:  # All groups
        practice_words = words.copy()
    else:
        practice_words = [w for w in words if w['group'] in selected_groups]

    if not practice_words:
        print("No words found for selected groups!")
        return

    # Sort by group, then by order in file (don't shuffle)
    practice_words.sort(key=lambda w: (w['group'], words.index(w)))

    # Track time
    start_time = datetime.now()
    session_id = start_time.strftime("%Y%m%d_%H%M%S")
    total_words = len(practice_words)

    # Navigation with back support
    i = 0
    while i < total_words:
        word = practice_words[i]
        current_num = i + 1

        # Show question (hidden answer)
        display_card(word, False, 0, 0, 0, practice_mode=True, current_num=current_num, total_num=total_words)
        debug_log(f"run_practice() showing card {current_num} of {total_words}")

        while True:
            key = get_key()
            debug_log(f"run_practice() question loop got key: {repr(key)}")
            if key is None:
                continue  # Ignore escape sequences
            if key == ' ':
                break
            elif key.lower() == 'b' and i > 0:
                # Go back to previous card
                i -= 1
                debug_log(f"run_practice() going back to card {i}")
                break
            elif check_for_quit(key):
                debug_log("run_practice() QUITTING from question loop")
                # Save practice time before quitting
                duration = (datetime.now() - start_time).total_seconds()
                save_practice_time(results_path, session_id, duration, selected_groups)
                clear_screen()
                print(f"\n  Practice session ended after viewing {current_num - 1} of {total_words} cards.")
                print(f"  Time spent: {format_duration(duration)}")
                print()
                input("  Press Enter to continue...")
                return

        # If we went back, continue to show the previous card
        if i < current_num - 1:
            continue

        # Show answer
        display_card(word, True, 0, 0, 0, practice_mode=True, current_num=current_num, total_num=total_words)
        debug_log(f"run_practice() showing answer for card {current_num}")

        while True:
            key = get_key()
            debug_log(f"run_practice() answer loop got key: {repr(key)}")
            if key is None:
                continue  # Ignore escape sequences
            if key == ' ':
                i += 1  # Move to next card
                break
            elif key.lower() == 'b' and i > 0:
                # Go back to previous card
                i -= 1
                debug_log(f"run_practice() going back to card {i}")
                break
            elif check_for_quit(key):
                debug_log("run_practice() QUITTING from answer loop")
                # Save practice time before quitting
                duration = (datetime.now() - start_time).total_seconds()
                save_practice_time(results_path, session_id, duration, selected_groups)
                clear_screen()
                print(f"\n  Practice session ended after viewing {current_num} of {total_words} cards.")
                print(f"  Time spent: {format_duration(duration)}")
                print()
                input("  Press Enter to continue...")
                return

    # Practice complete
    duration = (datetime.now() - start_time).total_seconds()
    save_practice_time(results_path, session_id, duration, selected_groups)

    clear_screen()
    GREEN = '\033[92m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    print(f"\n  {BOLD}╔══════════════════════════════════════════╗{RESET}")
    print(f"  {BOLD}║       📖 PRACTICE COMPLETE! 📖            ║{RESET}")
    print(f"  {BOLD}╠══════════════════════════════════════════╣{RESET}")
    print(f"  {BOLD}║{RESET}                                          {BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}   Cards reviewed: {total_words:<22} {BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}   Time spent: {format_duration(duration):<26} {BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}                                          {BOLD}║{RESET}")
    print(f"  {BOLD}╚══════════════════════════════════════════╝{RESET}")
    print()
    input("  Press Enter to continue...")


def run_quiz(words, selected_groups, results_path, mistake_mode=None, all_results=None):
    """Run the flashcard quiz."""
    # Flush any pending input (e.g., from mouse events)
    flush_stdin()

    # Filter words by selected groups or mistakes
    if mistake_mode and all_results:
        quiz_words = get_mistake_words(words, all_results, mistake_mode)
        if not quiz_words:
            clear_screen()
            print(f"\n  No mistakes found for the selected time period!")
            print("  Great job - or try practicing first!\n")
            input("  Press Enter to continue...")
            return
    elif 0 in selected_groups:  # All groups
        quiz_words = words.copy()
    else:
        quiz_words = [w for w in words if w['group'] in selected_groups]

    if not quiz_words:
        print("No words found for selected groups!")
        return

    # Shuffle the words
    random.shuffle(quiz_words)

    # Session tracking
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    correct_count = 0
    incorrect_count = 0

    # Queue: list of (word, times_correct_needed)
    queue = [(w, 0) for w in quiz_words]

    # Track words that need repetition
    repeat_words = {}

    position = 0

    while queue:
        word, needed_correct = queue.pop(0)
        remaining = len(queue) + 1

        # Add any repeat words that are due
        words_to_insert = []
        for pinyin, (rw, streak_needed, insert_pos) in list(repeat_words.items()):
            if position >= insert_pos:
                words_to_insert.append((rw, streak_needed))
                del repeat_words[pinyin]

        for rw, streak in words_to_insert:
            insert_idx = random.randint(0, min(3, len(queue)))
            queue.insert(insert_idx, (rw, streak))

        remaining = len(queue) + 1

        # Show question
        display_card(word, False, correct_count, incorrect_count, remaining)

        while True:
            key = get_key()
            if key is None:
                continue  # Ignore escape sequences
            if key == ' ':
                break
            elif check_for_quit(key):
                show_final_score(correct_count, incorrect_count)
                return

        # Show answer
        display_card(word, True, correct_count, incorrect_count, remaining)

        while True:
            key = get_key()
            if key is None:
                continue  # Ignore escape sequences
            if key == ' ':
                correct_count += 1
                save_result(results_path, word, True, session_id, selected_groups)

                if needed_correct > 1:
                    insert_pos = position + random.randint(5, 10)
                    repeat_words[word['pinyin']] = (word, needed_correct - 1, insert_pos)
                break
            elif key.lower() == 'x':
                incorrect_count += 1
                save_result(results_path, word, False, session_id, selected_groups)

                insert_pos = position + random.randint(5, 10)
                repeat_words[word['pinyin']] = (word, 2, insert_pos)
                break
            elif check_for_quit(key):
                show_final_score(correct_count, incorrect_count)
                return

        position += 1

    # Process remaining repeat words
    while repeat_words:
        pending = [(w, streak) for w, streak, _ in repeat_words.values()]
        repeat_words.clear()

        for word, streak_needed in pending:
            remaining = len(pending)

            display_card(word, False, correct_count, incorrect_count, remaining)

            while True:
                key = get_key()
                if key is None:
                    continue  # Ignore escape sequences
                if key == ' ':
                    break
                elif check_for_quit(key):
                    show_final_score(correct_count, incorrect_count)
                    return

            display_card(word, True, correct_count, incorrect_count, remaining)

            while True:
                key = get_key()
                if key is None:
                    continue  # Ignore escape sequences
                if key == ' ':
                    correct_count += 1
                    save_result(results_path, word, True, session_id, selected_groups)
                    if streak_needed > 1:
                        repeat_words[word['pinyin']] = (word, streak_needed - 1, 0)
                    break
                elif key.lower() == 'x':
                    incorrect_count += 1
                    save_result(results_path, word, False, session_id, selected_groups)
                    repeat_words[word['pinyin']] = (word, 2, 0)
                    break
                elif check_for_quit(key):
                    show_final_score(correct_count, incorrect_count)
                    return

    # Quiz complete
    show_final_score(correct_count, incorrect_count, complete=True)


def display_history(results, words):
    """Display session history."""
    clear_screen()

    if not results:
        print("\n  No session history yet! Start practicing or testing to see history.\n")
        input("  Press Enter to continue...")
        return

    BOLD = '\033[1m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

    # Group results by session
    sessions = defaultdict(lambda: {
        'start': None,
        'end': None,
        'is_practice': False,
        'results': [],
        'groups': set()
    })

    for r in results:
        session_id = r['session_id']
        sessions[session_id]['results'].append(r)

        if r['character'] == '_practice_':
            sessions[session_id]['is_practice'] = True
            # Extract groups from practice marker
            if r['group'] == -1:
                # Multiple groups encoded in pinyin
                import re
                match = re.search(r'_practice_(?:start|end)_([0-9,]+)_', r['pinyin'])
                if match:
                    groups_str = match.group(1)
                    sessions[session_id]['groups'] = set(map(int, groups_str.split(',')))
            elif r['group'] == 0:
                sessions[session_id]['groups'] = {0}  # All groups
            else:
                sessions[session_id]['groups'] = {r['group']}
        else:
            # Regular test result - add this word's group
            sessions[session_id]['groups'].add(r['group'])

        if sessions[session_id]['start'] is None or r['timestamp'] < sessions[session_id]['start']:
            sessions[session_id]['start'] = r['timestamp']
        if sessions[session_id]['end'] is None or r['timestamp'] > sessions[session_id]['end']:
            sessions[session_id]['end'] = r['timestamp']

    # Sort sessions by start time (most recent first)
    sorted_sessions = sorted(sessions.items(), key=lambda x: x[1]['start'], reverse=True)

    print()
    print(f"  {BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"  {BOLD}║                    📜 SESSION HISTORY 📜                      ║{RESET}")
    print(f"  {BOLD}╠══════════════════════════════════════════════════════════════╣{RESET}")
    print()

    # Show last 10 sessions (oldest to newest, so most recent is at bottom)
    for session_id, session_data in sorted_sessions[:10][::-1]:
        start = session_data['start']
        end = session_data['end']
        is_practice = session_data['is_practice']
        groups_set = session_data['groups']

        # Calculate duration
        if start and end:
            duration = (end - start).total_seconds()
            duration_str = format_duration(duration)
        else:
            duration_str = "?"

        # Format date
        date_str = start.strftime("%Y-%m-%d %H:%M")

        # Count cards and format session type
        if is_practice:
            session_type = f"{CYAN}PRACTICE{RESET}"
        else:
            # Test sessions
            results_list = session_data['results']
            correct = sum(1 for r in results_list if r['correct'])
            incorrect = sum(1 for r in results_list if not r['correct'])
            total = correct + incorrect
            if total > 0:
                accuracy = correct / total * 100
                acc_color = GREEN if accuracy >= 80 else YELLOW if accuracy >= 60 else RED
                session_type = f"TEST    {acc_color}({accuracy:.0f}% acc){RESET}"
            else:
                session_type = "TEST"

        # Format groups
        if 0 in groups_set:
            groups_display = "All groups"
        elif len(groups_set) > 3:
            groups_list = sorted(groups_set)
            groups_display = f"Groups {groups_list[0]},{groups_list[1]},{groups_list[2]}... ({len(groups_set)} total)"
        elif len(groups_set) > 1:
            groups_display = f"Groups {','.join(map(str, sorted(groups_set)))}"
        elif len(groups_set) == 1:
            group_num = list(groups_set)[0]
            groups_display = f"Group {group_num}"
        else:
            groups_display = "Unknown"

        print(f"  {BOLD}{date_str}{RESET}  │  {session_type}  │  {groups_display}")
        print(f"      Duration: {duration_str}")
        print()

    if len(sorted_sessions) > 10:
        print(f"  {YELLOW}(Showing last 10 sessions of {len(sorted_sessions)} total){RESET}")
        print()

    print()
    input("  Press Enter to continue...")


def show_final_score(correct_count, incorrect_count, complete=False):
    """Display final score."""
    clear_screen()

    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    if complete:
        print(f"\n  {BOLD}╔══════════════════════════════════════════╗{RESET}")
        print(f"  {BOLD}║          🎉 QUIZ COMPLETE! 🎉             ║{RESET}")
        print(f"  {BOLD}╠══════════════════════════════════════════╣{RESET}")
    else:
        print(f"\n  {BOLD}╔══════════════════════════════════════════╗{RESET}")
        print(f"  {BOLD}║            SESSION ENDED                  ║{RESET}")
        print(f"  {BOLD}╠══════════════════════════════════════════╣{RESET}")

    print(f"  {BOLD}║{RESET}                                          {BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}   {GREEN}Correct:   {correct_count:<28}{RESET} {BOLD}║{RESET}")
    print(f"  {BOLD}║{RESET}   {RED}Incorrect: {incorrect_count:<28}{RESET} {BOLD}║{RESET}")

    total = correct_count + incorrect_count
    if total > 0:
        pct = correct_count / total * 100
        color = GREEN if pct >= 80 else YELLOW if pct >= 60 else RED
        print(f"  {BOLD}║{RESET}   {color}Accuracy:  {pct:.1f}%{' ' * (27 - len(f'{pct:.1f}%'))}{RESET} {BOLD}║{RESET}")

    print(f"  {BOLD}║{RESET}                                          {BOLD}║{RESET}")
    print(f"  {BOLD}╚══════════════════════════════════════════╝{RESET}")
    print()
    input("  Press Enter to continue...")


def main():
    """Main entry point."""
    script_dir = Path(__file__).parent
    csv_path = script_dir / 'words.csv'
    results_path = script_dir / 'results.csv'

    if not csv_path.exists():
        print(f"Error: {csv_path} not found!")
        sys.exit(1)

    words = load_words(csv_path)
    groups = get_groups(words)

    while True:
        results = load_results(results_path)
        has_results = len(results) > 0

        choice = display_menu(groups, has_results)

        if choice == 'quit':
            clear_screen()
            print("\n  再见! (Zàijiàn - Goodbye!)\n")
            break
        elif choice == 's':
            display_stats(results, words, results_path)
        elif choice == 'h':
            display_history(results, words)
        elif choice == 'd':
            run_quiz(words, [], results_path, mistake_mode='day', all_results=results)
        elif choice == 'w':
            run_quiz(words, [], results_path, mistake_mode='week', all_results=results)
        elif choice == 'm':
            run_quiz(words, [], results_path, mistake_mode='month', all_results=results)
        elif choice == 'p':
            run_practice(words, [0], results_path)
        elif choice == 'pd':
            run_practice(words, [], results_path, mistake_mode='day', all_results=results)
        elif choice == 'pw':
            run_practice(words, [], results_path, mistake_mode='week', all_results=results)
        elif choice == 'pm':
            run_practice(words, [], results_path, mistake_mode='month', all_results=results)
        elif choice.startswith('p') and len(choice) > 1:
            # Practice specific group (e.g., 'p3' for group 3)
            try:
                group_num = int(choice[1:])
                if group_num in groups:
                    run_practice(words, [group_num], results_path)
                else:
                    print(f"  Invalid group: {group_num}")
                    input("  Press Enter to continue...")
            except ValueError:
                print("  Invalid input. Use 'p' followed by group number (e.g., 'p3').")
                input("  Press Enter to continue...")
        else:
            try:
                group_num = int(choice)
                if group_num == 0:
                    run_quiz(words, [0], results_path)
                elif group_num in groups:
                    run_quiz(words, [group_num], results_path)
                else:
                    print(f"  Invalid group: {group_num}")
                    input("  Press Enter to continue...")
            except ValueError:
                print("  Invalid input. Please enter a number or letter option.")
                input("  Press Enter to continue...")


if __name__ == '__main__':
    main()
