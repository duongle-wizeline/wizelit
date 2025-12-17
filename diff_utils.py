# wizelit/diff_utils.py
import difflib
import plotly.graph_objects as go

def generate_inline_diff(original_code: str, new_code: str) -> str:
    """Standard unified diff for text fallback"""
    diff = difflib.unified_diff(
        original_code.splitlines(keepends=True),
        new_code.splitlines(keepends=True),
        fromfile="Original",
        tofile="Refactored",
    )
    return "".join(diff)

def generate_plotly_diff(original_code: str, new_code: str):
    """
    Generates a Plotly Figure containing a side-by-side table comparison.
    """
    # Helper to preserve whitespace and newlines in Plotly Table
    def format_for_plotly(code_str):
        # Replace spaces with non-breaking spaces to keep indentation
        # Replace newlines with <br> to keep structure
        return code_str.replace(" ", "&nbsp;").replace("\n", "<br>")

    # Prepare data for columns
    col1_content = format_for_plotly(original_code)
    col2_content = format_for_plotly(new_code)

    # Create the Table
    fig = go.Figure(data=[go.Table(
        header=dict(
            values=['<b>🔴 BEFORE</b>', '<b>🟢 AFTER</b>'],
            line_color='#e5e7eb',
            fill_color='#f9fafb',
            align='left',
            font=dict(color='black', size=12)
        ),
        cells=dict(
            values=[[col1_content], [col2_content]], # Each column is one giant cell for code
            line_color='#e5e7eb',
            fill_color='white',
            align='left',
            font=dict(family="monospace", color='#333', size=11),
            height=30 # Minimum row height
        )
    )])

    # Update Layout to look clean
    fig.update_layout(
        margin=dict(l=5, r=5, t=10, b=10),
        height=max(500, len(original_code.splitlines()) * 20), # Dynamic height
        autosize=True
    )

    return fig