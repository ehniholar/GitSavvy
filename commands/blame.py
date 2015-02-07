import re
from collections import namedtuple, defaultdict
from functools import lru_cache

import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand
from ..common.util import dates


BlamedLine = namedtuple("BlamedLine", ("contents", "commit_hash", "orig_lineno", "final_lineno"))

NOT_COMMITED_HASH = "0000000000000000000000000000000000000000"
BLAME_TITLE = "BLAME: {}"


class GsBlameCommand(WindowCommand, BaseCommand):

    def run(self, file_path=None, repo_path=None):
        repo_path = repo_path or self.repo_path
        file_path = file_path or self.file_path
        view = self.window.new_file()
        view.set_syntax_file("Packages/GitSavvy/syntax/blame.tmLanguage")
        view.settings().set("git_savvy.blame_view", True)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.file_path", file_path)
        view.settings().set("word_wrap", False)
        view.settings().set("line_numbers", False)
        view.settings().set('indent_guide_options', [])
        view.set_name(BLAME_TITLE.format(self.get_rel_path(file_path)))
        view.set_scratch(True)
        view.run_command("gs_blame_initialize_view")


class GsBlameInitializeViewCommand(TextCommand, BaseCommand):

    def run(self, edit):
        content = self.get_content()
        self.view.sel().clear()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(True)

    def get_content(self):
        blame_porcelain = self.git("blame", "-p", self.file_path)
        blamed_lines, commits = self.parse_blame(blame_porcelain.splitlines())

        commit_infos = {
            commit_hash: self.short_commit_info(commit)
            for commit_hash, commit in commits.items()
        }

        partitions = tuple(self.partition(blamed_lines))

        longest_commit_line = max(
            (line
             for commit_info in commit_infos.values()
             for line in commit_info),
            key=len)

        longest_code_line = max(
            (line.contents for partition in partitions for line in partition),
            key=len
        )

        partitions_with_commits_iter = self.couple_partitions_and_commits(
            partitions=partitions,
            commit_infos=commit_infos,
            left_pad=len(longest_commit_line)
        )

        spacer = (
            "-" * len(longest_commit_line) +
            " | " +
            "-" * (5 + len(longest_code_line)) +
            "\n"
        )

        return spacer.join(partitions_with_commits_iter)

    def parse_blame(self, blame_porcelain):
        lines_iter = iter(blame_porcelain)

        blamed_lines = []
        commits = defaultdict(lambda: defaultdict(str))

        for line in lines_iter:
            commit_hash, orig_lineno, final_lineno, _ = \
                re.match(r"([0-9a-f]{40}) (\d+) (\d+)( \d+)?", line).groups()
            commits[commit_hash]["short_hash"] = commit_hash[:12]
            commits[commit_hash]["long_hash"] = commit_hash

            next_line = next(lines_iter)
            while not next_line.startswith("\t"):
                # Iterate through header keys and values.
                k, v = re.match(r"([^ ]+) (.+)", next_line).groups()
                commits[commit_hash][k] = v
                next_line = next(lines_iter)

            # If `next_lines` starts with a tab (and breaks out of the above
            # while loop), it is an actual line of code.  The line following
            # that will be a new header or the end of the file.
            blamed_lines.append(BlamedLine(
                # Strip tab character.
                contents=next_line[1:],
                commit_hash=commit_hash,
                orig_lineno=orig_lineno,
                final_lineno=final_lineno))

        return blamed_lines, commits

    @staticmethod
    def partition(blamed_lines):
        prev_line = None
        current_hunk = []
        for line in blamed_lines:
            if prev_line and line.commit_hash != prev_line.commit_hash:
                yield current_hunk
                current_hunk = []

            prev_line = line
            current_hunk.append(line)
        yield current_hunk

    cache = {}

    @staticmethod
    def short_commit_info(commit):
        if commit["long_hash"] == NOT_COMMITED_HASH:
            return ("Not committed yet.", )

        summary = commit["summary"]
        if len(summary) > 40:
            summary = summary[:36] + " ..."
        author_info = commit["author"] + " " + commit["author-mail"]
        time_stamp = dates.fuzzy(commit["author-time"]) if commit["author-time"] else ""

        return (summary, commit["short_hash"], author_info, time_stamp)

    @staticmethod
    def couple_partitions_and_commits(partitions, commit_infos, left_pad):
        left_fallback = " " * left_pad
        right_fallback = ""

        for partition in partitions:
            output = ""
            commit_info = commit_infos[partition[0].commit_hash]
            left_len = len(commit_info)
            right_len = len(partition)
            total_lines = max(left_len, right_len)
            total_lines = len(max((commit_info, partition), key=len))

            for i in range(total_lines):
                left = commit_info[i] if i < left_len else left_fallback
                right = partition[i].contents if i < right_len else right_fallback
                lineno = partition[i].final_lineno if i < right_len else right_fallback
                output += "{left: <{left_pad}} | {lineno: >4} {right}\n".format(
                    left=left,
                    left_pad=left_pad,
                    lineno=lineno,
                    right=right)

            yield output
