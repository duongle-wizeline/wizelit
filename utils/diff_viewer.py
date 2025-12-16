import difflib
from pathlib import Path

def html_diff_viewer(from_lines: list[str], to_lines: list[str]) -> str:
    """Compare two texts and generate an HTML difference view."""
    # Create an HtmlDiff instance
    # You can use parameters like wrapcolumn=80 to control line wrapping
    differ = difflib.HtmlDiff()

    # Generate the HTML output (as a single string)
    # make_file includes full HTML boilerplate (<head>, <body>, etc.)
    full_html_diff = differ.make_file(from_lines, to_lines, fromdesc='Original', todesc='Modified')
    html_diff = differ.make_table(from_lines, to_lines, fromdesc='Original', todesc='Modified')

    # Write the HTML to a file
    output_path = Path("text_comparison_diff.html")
    try:
        output_path.write_text(full_html_diff, encoding='utf-8')
        print(f"Comparison saved to {output_path.resolve()}")
    except IOError as e:
        print(f"Error writing file: {e}")

    return html_diff

# To view the result, open the generated 'text_comparison_diff.html' file in a web browser.
