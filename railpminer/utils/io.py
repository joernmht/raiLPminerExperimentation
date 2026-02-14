"""File I/O utilities: input processing, text preprocessing, usage parsing."""

import re
from pathlib import Path

import pandas as pd


def process_inputfiles(input_directory, output_name, counter_start=1, file_suffix=""):
    """Process files from a directory and return their contents as a DataFrame.

    Args:
        input_directory: Path to the directory containing files.
        output_name: Base name for the output variables (e.g. "Paper").
        counter_start: Starting number for the counter.
        file_suffix: Suffix that files must end with to be processed.

    Returns:
        DataFrame with columns ``variable_name``, ``paper``, ``text``.
    """
    input_dir = Path(input_directory)

    counter = counter_start
    variable_names = []
    file_names = []
    contents = []

    if input_dir.exists() and input_dir.is_dir():
        for file_path in input_dir.iterdir():
            if file_path.is_file() and file_path.name.endswith(file_suffix):
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_content = file.read()

                variable_name = f"{output_name}_{counter}"
                variable_names.append(variable_name)
                file_names.append(file_path.name.replace(file_suffix, ""))
                contents.append(file_content)
                counter += 1

        df = pd.DataFrame({
            'variable_name': variable_names,
            'paper': file_names,
            'text': contents,
        })

        if len(df) > 0:
            print(f"Successfully processed {len(df)} files.")
            print(f"Created variables: {', '.join(variable_names)}")
        else:
            print(f"No files ending with '{file_suffix}' were found.")
    else:
        print(f"Directory '{input_dir}' does not exist.")
        df = pd.DataFrame(columns=['variable_name', 'paper', 'text'])

    return df


def preprocess_model_text(text):
    """Replace Unicode math characters with ASCII equivalents."""
    replacements = {
        '\u2265': '>=',
        '\u2264': '<=',
        '\u2211': 'sum',
        '\u2192': '->',
        '\u03c4': 'tau',
        '\u00b7': '*',
        '\u2208': 'in',
    }
    for unicode_char, ascii_replacement in replacements.items():
        text = text.replace(unicode_char, ascii_replacement)
    return text


def parse_usage(s):
    """Extract token usage numbers from a usage string.

    Returns:
        ``pd.Series`` with keys like ``requests``, ``request_tokens``, etc.
    """
    matches = re.findall(r'(\w+)=([0-9]+)', s)
    d = {k: int(v) for k, v in matches}
    cached = re.search(r"cached_tokens': (\d+)", s)
    if cached:
        d['cached_tokens'] = int(cached.group(1))
    return pd.Series(d)
