#!/usr/bin/env python3
"""
Extract column name to human-readable label mappings from definitions.html
and generate a CSV lookup table for the data exploration UI.
"""

import re
import csv
import logging
from html.parser import HTMLParser
from collections import OrderedDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_variable_definitions(html_content):
    """
    Parse the definitions HTML and extract variable names with their labels.

    Returns a list of tuples: (dataset, variable_name, label, data_type)
    """
    results = []

    # Pattern 1: Variables with direct label after variable name
    # <td><a id="IG.ADSL.IT.ADSL.ARM"></a>ARM</td><td>Description of Planned Arm</td>
    pattern1 = re.compile(
        r'<td><a\s+id="IG\.([^.]+)\.IT\.[^.]+\.([^"]+)"></a>([^<]+)</td>\s*<td>([^<]+)</td>',
        re.IGNORECASE
    )

    # Pattern 2: Variables with empty td between variable name and label
    # <td><a id="IG.ADADAS.IT.ADADAS.VISITNUM"></a>VISITNUM</td><td></td><td>Visit Number</td>
    pattern2 = re.compile(
        r'<td><a\s+id="IG\.([^.]+)\.IT\.[^.]+\.([^"]+)"></a>([^<]+)</td>\s*<td></td>\s*<td>([^<]+)</td>',
        re.IGNORECASE
    )

    # Pattern 3: Variables with VLM span (value level metadata)
    # <td>AVAL<span class="valuelist-reference"...><a id="IG.ADADAS.IT.ADADAS.AVAL">VLM</a></span></td><td></td><td>Analysis Value</td>
    pattern3 = re.compile(
        r'<td>([A-Z0-9_]+)<span[^>]*><a\s+id="IG\.([^.]+)\.IT\.[^.]+\.[^"]+">VLM</a></span></td>\s*<td></td>\s*<td>([^<]+)</td>',
        re.IGNORECASE
    )

    # Extract matches from pattern 2 first (more specific - has empty td)
    for match in pattern2.finditer(html_content):
        dataset = match.group(1)
        var_id = match.group(2)
        var_name = match.group(3).strip()
        label = match.group(4).strip()
        if label and var_name and not label.startswith('<'):
            results.append((dataset, var_name, label, ''))

    # Extract matches from pattern 1
    for match in pattern1.finditer(html_content):
        dataset = match.group(1)
        var_id = match.group(2)
        var_name = match.group(3).strip()
        label = match.group(4).strip()
        # Skip if this looks like it's part of a pattern2 match (empty label)
        if label and var_name and not label.startswith('<') and label != '':
            # Check if we already have this variable from pattern2
            key = (dataset, var_name)
            if not any(r[0] == dataset and r[1] == var_name for r in results):
                results.append((dataset, var_name, label, ''))

    # Extract VLM variables
    for match in pattern3.finditer(html_content):
        var_name = match.group(1).strip()
        dataset = match.group(2)
        label = match.group(3).strip()
        if label and var_name:
            key = (dataset, var_name)
            if not any(r[0] == dataset and r[1] == var_name for r in results):
                results.append((dataset, var_name, label, ''))

    return results


def extract_dataset_definitions(html_content):
    """
    Extract dataset-level definitions (dataset name -> description).
    """
    results = []

    # Pattern for dataset definitions in the datasets summary table
    # <tr class="tablerowodd" id="IG.ADSL"><td><a href="#IG.IG.ADSL">ADSL</a>...
    # </td><td>Subject-Level Analysis</td>
    pattern = re.compile(
        r'<tr[^>]*id="IG\.([^"]+)"[^>]*>\s*<td><a[^>]*>([^<]+)</a>.*?</td>\s*<td>([^<]+)</td>',
        re.IGNORECASE | re.DOTALL
    )

    for match in pattern.finditer(html_content):
        dataset = match.group(1)
        dataset_name = match.group(2).strip()
        description = match.group(3).strip()
        if description and dataset_name:
            results.append((dataset_name, '', description, 'DATASET'))

    return results


def extract_with_beautiful_soup(html_content):
    """
    Alternative extraction using regex patterns for more robust parsing.
    """
    results = []
    seen = set()

    # More comprehensive pattern that captures the data type too
    # Handles both cases: label right after var, or empty td then label

    # Pattern for variables with datatype info
    pattern_full = re.compile(
        r'<td><a\s+id="IG\.([^.]+)\.IT\.[^.]+\.([^"]+)"></a>([^<]+)</td>'
        r'(?:\s*<td></td>)?'  # Optional empty td
        r'\s*<td>([^<]*)</td>'  # Label (may be empty)
        r'\s*<td\s+class="datatype">([^<]+)</td>',  # Data type
        re.IGNORECASE
    )

    for match in pattern_full.finditer(html_content):
        dataset = match.group(1)
        var_id = match.group(2)
        var_name = match.group(3).strip()
        label = match.group(4).strip()
        data_type = match.group(5).strip()

        key = (dataset, var_name)
        if key not in seen and var_name:
            seen.add(key)
            # Use var_name as label if label is empty
            final_label = label if label else var_name
            results.append((dataset, var_name, final_label, data_type))

    return results


def consolidate_results(results):
    """
    Consolidate results to create a master lookup with unique column names.
    Also creates per-dataset mappings.
    """
    # Dictionary: column_name -> {datasets: [...], labels: [...], most_common_label}
    column_lookup = {}

    for dataset, var_name, label, data_type in results:
        if not var_name:
            continue

        if var_name not in column_lookup:
            column_lookup[var_name] = {
                'datasets': [],
                'labels': [],
                'data_types': []
            }

        column_lookup[var_name]['datasets'].append(dataset)
        if label:
            column_lookup[var_name]['labels'].append(label)
        if data_type:
            column_lookup[var_name]['data_types'].append(data_type)

    # Determine the most common/best label for each column
    consolidated = []
    for var_name, info in column_lookup.items():
        # Use the most common label, or the first one if all unique
        if info['labels']:
            label_counts = {}
            for lbl in info['labels']:
                label_counts[lbl] = label_counts.get(lbl, 0) + 1
            best_label = max(label_counts.items(), key=lambda x: x[1])[0]
        else:
            best_label = var_name  # Fallback to var name

        # Get most common data type
        if info['data_types']:
            type_counts = {}
            for dt in info['data_types']:
                type_counts[dt] = type_counts.get(dt, 0) + 1
            best_type = max(type_counts.items(), key=lambda x: x[1])[0]
        else:
            best_type = ''

        datasets = sorted(set(info['datasets']))
        consolidated.append({
            'column_name': var_name,
            'label': best_label,
            'data_type': best_type,
            'datasets': ','.join(datasets)
        })

    return consolidated


def main():
    # Read the HTML file
    with open('definitions.html', 'r', encoding='utf-8') as f:
        html_content = f.read()

    logger.info("Extracting variable definitions from definitions.html...")

    # Extract using both methods and combine
    results1 = extract_variable_definitions(html_content)
    results2 = extract_with_beautiful_soup(html_content)

    # Combine results, preferring results2 (has data types)
    seen = set()
    combined = []

    for r in results2:
        key = (r[0], r[1])
        if key not in seen:
            seen.add(key)
            combined.append(r)

    for r in results1:
        key = (r[0], r[1])
        if key not in seen:
            seen.add(key)
            combined.append(r)

    # Add dataset definitions
    dataset_defs = extract_dataset_definitions(html_content)
    for r in dataset_defs:
        combined.append(r)

    logger.info(f"Found {len(combined)} total variable/dataset definitions")

    # Consolidate into master lookup
    consolidated = consolidate_results(combined)

    logger.info(f"Consolidated into {len(consolidated)} unique column names")

    # Write detailed CSV with all info
    output_file = 'column_labels_lookup.csv'
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['column_name', 'label', 'data_type', 'datasets'])
        writer.writeheader()
        for row in sorted(consolidated, key=lambda x: x['column_name']):
            writer.writerow(row)

    logger.info(f"Wrote {output_file}")

    # Also write a simple column_name -> label mapping
    simple_output = 'column_labels_simple.csv'
    with open(simple_output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['column_name', 'label'])
        for row in sorted(consolidated, key=lambda x: x['column_name']):
            writer.writerow([row['column_name'], row['label']])

    logger.info(f"Wrote {simple_output}")

    return consolidated


if __name__ == '__main__':
    main()
