# Copyright 2020 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""
Predefined step definitions for handling dialog interaction with Mycroft for
use with behave.
"""
import logging
from os.path import join, exists, basename
from glob import glob
import re
import time
from pathlib import Path

from behave import given, when, then

from mycroft.messagebus import Message

from test.integrationtests.voight_kampff import (
    mycroft_responses,
    then_wait,
    then_wait_fail,
)

LOG = logging.getLogger("Voight Kampff")

# def find_dialog(skill_path, dialog, lang):
#     """Check the usual location for dialogs.

#     TODO: subfolders
#     """
#     if exists(join(skill_path, "dialog")):
#         return join(skill_path, "dialog", lang, dialog)
#     else:
#         return join(skill_path, "locale", lang, dialog)


# def load_dialog_file(dialog_path):
#     """Load dialog files and get the contents."""
#     with open(dialog_path) as f:
#         lines = f.readlines()
#     return [l.strip().lower() for l in lines if l.strip() != "" and l.strip()[0] != "#"]


# def load_dialog_list(skill_path, dialog):
#     """Load dialog from files into a single list.

#     Args:
#         skill (MycroftSkill): skill to load dialog from
#         dialog (list): Dialog names (str) to load

#     Returns:
#         tuple (list of Expanded dialog strings, debug string)
#     """
#     dialog_path = find_dialog(skill_path, dialog)

#     debug = "Opening {}\n".format(dialog_path)
#     return load_dialog_file(dialog_path), debug


# def dialog_from_sentence(sentence, skill_path, lang):
#     """Find dialog file from example sentence.

#     Args:
#         sentence (str): Text to match
#         skill_path (str): path to skill directory
#         lang (str): language code to use

#     Returns (str): Dialog file best matching the sentence.
#     """
#     dialog_paths = join(skill_path, "dialog", lang, "*.dialog")
#     best = (None, 0)
#     for path in glob(dialog_paths):
#         patterns = load_dialog_file(path)
#         match, _ = _match_dialog_patterns(patterns, sentence.lower())
#         if match is not False:
#             if len(patterns[match]) > best[1]:
#                 best = (path, len(patterns[match]))
#     if best[0] is not None:
#         return basename(best[0])
#     else:
#         return None


# def _match_dialog_patterns(dialogs, sentence):
#     """Match sentence against a list of dialog patterns.

#     Returns index of found match.
#     """
#     # Allow custom fields to be anything
#     dialogs = [re.sub(r"{.*?\}", r".*", dia) for dia in dialogs]
#     # Remove left over '}'
#     dialogs = [re.sub(r"\}", r"", dia) for dia in dialogs]
#     dialogs = [re.sub(r" .* ", r" .*", dia) for dia in dialogs]
#     # Merge consequtive .*'s into a single .*
#     dialogs = [re.sub(r"\.\*( \.\*)+", r".*", dia) for dia in dialogs]
#     # Remove double whitespaces
#     dialogs = ["^" + " ".join(dia.split()) for dia in dialogs]
#     debug = "MATCHING: {}\n".format(sentence)
#     for index, regex in enumerate(dialogs):
#         match = re.match(regex, sentence)
#         debug += "---------------\n"
#         debug += "{} {}\n".format(regex, match is not None)
#         if match:
#             return index, debug
#     else:
#         return False, debug


@given("an english speaking user")
def given_english(context):
    context.lang = "en-us"


# @given("a {timeout} seconds timeout")
# @given("a {timeout} second timeout")
# def given_timeout(context, timeout):
#     """Set the timeout for the steps in this scenario."""
#     context.step_timeout = float(timeout)


# @given("a {timeout} minutes timeout")
# @given("a {timeout} minute timeout")
# def given_timeout(context, timeout):
#     """Set the timeout for the steps in this scenario."""
#     context.step_timeout = float(timeout) * 60


@when('the user says "{text}"')
def when_user_says(context, text: str):
    context.client.say_utterance(text)


@then('"{skill}" should reply with dialog from "{dialog}"')
def then_dialog(context, skill: str, dialog: str):
    context.client.match_dialogs_or_fail(dialog, skill_id=skill)


@then('"{skill}" should reply with dialog from list "{dialog}"')
def then_dialog_list(context, skill: str, dialog: str):
    """Handle list of possible dialogs, separated by semi-colon (;)"""
    context.client.match_dialogs_or_fail(dialog, skill_id=skill)


# @then('"{skill}" should not reply')
# def then_do_not_reply(context, skill):
#     def check_all_dialog(message):
#         msg_skill = message.data.get("meta").get("skill")
#         utt = message.data["utterance"].lower()
#         skill_responded = skill == msg_skill
#         debug_msg = (
#             "{} responded with '{}'. \n".format(skill, utt) if skill_responded else ""
#         )
#         return (skill_responded, debug_msg)

#     passed, debug = then_wait_fail("speak", check_all_dialog, context)
#     if not passed:
#         assert_msg = debug
#         assert_msg += mycroft_responses(context)
#     assert passed, assert_msg or "{} responded".format(skill)


# @then('"{skill}" should reply with "{example}"')
# def then_example(context, skill, example):
#     skill_path = context.msm.find_skill(skill).path
#     dialog = dialog_from_sentence(example, skill_path, context.lang)
#     print("Matching with the dialog file: {}".format(dialog))
#     assert dialog is not None, "No matching dialog..."
#     then_dialog(context, skill, dialog)


# @then('"{skill}" should reply with anything')
# def then_anything(context, skill):
#     def check_any_messages(message):
#         debug = ""
#         result = message is not None
#         return (result, debug)

#     passed = then_wait("speak", check_any_messages, context)
#     assert passed, "No speech received at all"


# @then('"{skill}" should reply with exactly "{text}"')
# def then_exactly(context, skill, text):
#     def check_exact_match(message):
#         utt = message.data["utterance"].lower()
#         debug = "Comparing {} with expected {}\n".format(utt, text)
#         result = utt == text.lower()
#         return (result, debug)

#     passed, debug = then_wait("speak", check_exact_match, context)
#     if not passed:
#         assert_msg = debug
#         assert_msg += mycroft_responses(context)
#     assert passed, assert_msg


@when('mycroft reply should contain "{text}"')
def then_contains(context, text: str):
    passed = False
    assert_message = "Mycroft didn't respond"
    text = text.lower().strip()

    maybe_message = context.client.get_next_speak()
    if maybe_message is not None:
        utterance = maybe_message.data.get("utterance", "")
        utterance = utterance.lower().strip()

        if text in utterance:
            passed = True
        else:
            assert_message = f"Did not find '{text}' in '{utterance}'"

    assert passed, assert_message


@then('the user replies with "{text}"')
@then('the user replies "{text}"')
@then('the user says "{text}"')
def then_user_follow_up(context, text):
    """Send a user response after being prompted by device."""
    context.client.wait_for_message("mycroft.mic.listen")
    context.client.say_utterance(text, response_skill_id=None)


@then('mycroft should send the message "{message_type}"')
def then_messagebus_message(context, message_type: str):
    """Verify a specific message is sent."""
    maybe_message = context.client.wait_for_message(message_type)
    assert maybe_message is not None, "No matching message received."


@then("dialog is stopped")
def dialog_is_stopped(context):
    context.client.bus.emit(Message("mycroft.tts.stop"))