#!/usr/bin/env python3
"""
KeepToOrg.py

Usage:
    python3 KeepToOrg.py /path/to/google/Keep output/dir

Given a Takeout of your Google Keep Notes in .html format, output .org files with logical groupings
based on tags. This will also format lists and try to be smart.
"""

import datetime
import html
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

# TODO:
# Format links:
#   Links have the syntax [[https://blah][Example link]] (things can be internal links too!)
#   See https://orgmode.org/manual/External-links.html

DATE_FORMAT = '%b %d, %Y, %I:%M:%S %p'
UNTAGGED_GROUP = 'Untagged'


def tags_to_org_string(tags: List[str]) -> str:
    """Convert a list of tags to an Emacs Org tag string (:tag1:tag2:)."""
    if not tags:
        return ''
    return ':' + ':'.join(tags) + ':'


@dataclass
class Note:
    title: str = ''
    body: str = ''
    tags: List[str] = field(default_factory=list)
    archived: bool = False
    date: datetime.datetime = datetime.datetime.min

    def to_org_string(self) -> str:
        # Work on copies so the note itself is not mangled
        body = self.body
        title = self.title

        # Convert lists to org lists. This is a total hack but works
        body = body.replace('<li class="listitem"><span class="bullet">&#9744;</span>\n', '- [ ] ')
        body = body.replace('<li class="listitem checked"><span class="bullet">&#9745;</span>', '- [X] ')

        # Flat out remove these
        for html_tag_to_erase in ('<span class="text">', '</span>', '</li>', '<ul class="list">', '</ul>'):
            body = body.replace(html_tag_to_erase, '')

        # Fix the edge case where the list entry has a new line before the content
        for list_prefix in ('- [ ] \n', '- [X] \n'):
            body = body.replace(list_prefix, list_prefix[:-1])

        # Unescape all (e.g. remove &quot; and replace with ")
        title = html.unescape(title)
        body = html.unescape(body)
        tags = [html.unescape(tag) for tag in self.tags]

        # Strip tags from the body
        for tag in tags:
            body = body.replace(f'#{tag}', '')

        # Remove any leading/trailing whitespace (possibly leftover from tag stripping)
        body = body.strip()

        # Make a title if necessary
        org_title = title
        if not org_title:
            to_newline = body.find('\n')
            if to_newline >= 0:
                # There's a line break; use the first line as a title
                org_title = body[:to_newline]
                body = body[len(org_title) + 1:]
            else:
                # The note has no breaks; make the body the title and clear the body
                org_title = body
                body = ''

        nesting = '*' if self.archived else ''
        header = f'*{nesting} {org_title}'
        tag_string = tags_to_org_string(tags)

        # Various levels of information require different formats
        parts = [header]
        if body:
            parts.append(body)
        if tag_string:
            parts.append(tag_string)
        return '\n'.join(parts)


def get_all_note_html_files(html_dir: Path) -> List[Path]:
    print(f'Looking for notes in {html_dir}')
    note_html_files = sorted(path for path in html_dir.rglob('*.html') if path.is_file())
    print(f'Found {len(note_html_files)} notes')
    return note_html_files


def get_html_value_if_matches(line: str, tag: str, end_tag: str) -> Tuple[str, bool]:
    lowered = line.lower()
    tag_lower = tag.lower()
    end_tag_lower = end_tag.lower()

    start = lowered.find(tag_lower)
    if start < 0:
        return '', False

    value_start = start + len(tag)
    end = lowered.rfind(end_tag_lower)
    if end < value_start:
        return '', False

    return line[value_start:end], True


def make_safe_filename(name: str) -> str:
    return name.replace('/', '').replace('.', '')


def parse_note(note_file_path: Path) -> Note:
    with note_file_path.open('r', encoding='utf-8') as note_file:
        note_lines = note_file.readlines()

    note = Note()
    read_state = 'lookingForAny'
    num_opened_divs = 0

    for line in note_lines:
        num_opened_divs += line.count('<div')
        num_opened_divs -= line.count('</div>')

        if read_state == 'lookingForAny':
            if '<span class="archived" title="Note archived">' in line:
                note.archived = True

            # Parse title
            title, is_match = get_html_value_if_matches(line, '<div class="title">', '</div>')
            if is_match:
                note.title = title
                continue

            if '<div class="content">' in line:
                read_state = 'parsingBody'
                # For same-line bodies, strip opening div
                line = line.replace('<div class="content">', '')

            # Parse the date (for sorting)
            if ' AM</div>' in line or ' PM</div>' in line:
                date_string = line.replace('</div>', '').strip()
                # Example: "Apr 27, 2018, 6:32:15 PM"
                try:
                    note.date = datetime.datetime.strptime(date_string, DATE_FORMAT)
                except ValueError:
                    print(f'Could not parse date "{date_string}" in {note_file_path}')

            # Parse tags, if any
            potential_tag, is_match = get_html_value_if_matches(
                line, '<span class="label-name">', '</span>')
            if is_match:
                note.tags.append(potential_tag)
                continue

        # Parse body
        if read_state == 'parsingBody':
            if line.strip().lower() == '<br>':
                line = '\n'

            if line.strip().lower().endswith('</div>') and num_opened_divs == 1:
                line = line.rstrip()[:-len('</div>')]
                read_state = 'lookingForAny'

            note.body += line.replace('<br>', '\n')

    return note


def group_notes_by_tag(notes: List[Note]) -> Dict[str, List[Note]]:
    note_groups: Dict[str, List[Note]] = defaultdict(list)
    for note in notes:
        for tag in note.tags:
            note_groups[tag].append(note)
        if not note.tags:
            note_groups[UNTAGGED_GROUP].append(note)
    return note_groups


def write_groups(note_groups: Dict[str, List[Note]], output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)

    num_notes_written = 0
    for tag, group in note_groups.items():
        out_file_path = output_dir / f'{make_safe_filename(tag)}.org'

        # If capture etc. appends, we should probably follow that same logic (don't reverse)
        notes_sorted_by_date = sorted(group, key=lambda n: n.date)

        lines: List[str] = []
        archived_lines: List[str] = []
        for note in notes_sorted_by_date:
            target = archived_lines if note.archived else lines
            target.append(note.to_org_string() + '\n')

        if archived_lines:
            lines = ['* *Archived*\n'] + archived_lines + lines

        with out_file_path.open('w', encoding='utf-8') as out_file:
            out_file.writelines(lines)

        print(f'Wrote {len(group)} notes to {out_file_path}')
        num_notes_written += len(group)

    return num_notes_written


def main(keep_html_dir: str, output_dir: str) -> None:
    note_files = get_all_note_html_files(Path(keep_html_dir))
    notes = [parse_note(path) for path in note_files]
    note_groups = group_notes_by_tag(notes)
    num_notes_written = write_groups(note_groups, Path(output_dir))
    print(f'Wrote {num_notes_written} notes total')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('Wrong number of arguments!\nUsage:\n\tpython3 KeepToOrg.py /path/to/google/Keep output/dir')
        sys.exit(1)

    main(sys.argv[1], sys.argv[2])
