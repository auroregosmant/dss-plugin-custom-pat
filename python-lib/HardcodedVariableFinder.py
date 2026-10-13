import ast
import re

class HardcodedVariableFinder(ast.NodeVisitor):
    """
    A custom AST NodeVisitor to find hardcoded variable assignments.
    It ignores:
    1. String assignments that contain the pattern '${variable_name}'.
    2. Empty list assignments (e.g., `output = []`).
    3. Empty dictionary assignments (e.g., `reference_dict = {}`).
    4. Single-character variable assignments to integer constants (e.g., `i = 0`).
    """
    def __init__(self):
        super().__init__()
        self.hardcoded_vars = []
        # Regex to find patterns like ${variable_name}
        # [a-zA-Z_] for the first character of the variable name
        # [a-zA-Z0-9_]* for subsequent characters
        self.variable_pattern = re.compile(r'\$\{[a-zA-Z_][a-zA-Z0-9_]*\}')

    def visit_Assign(self, node):
        """
        Visits assignment nodes to identify hardcoded values,
        applying the exclusion rules.
        
        Args:
            node: The AST Assign node being visited.
        """
        # Check if the assigned value is a constant type (string, number, list, tuple, dict)
        if isinstance(node.value, (ast.Constant, ast.Num, ast.Str, ast.List, ast.Tuple, ast.Dict)):
            
            # Iterate through all targets of the assignment (e.g., a, b = 1, 2)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_name = target.id

                    # Rule 1: Special handling for string literals with placeholders
                    if isinstance(node.value, ast.Str):
                        if self.variable_pattern.search(node.value.s):
                            self.generic_visit(node) # Continue visiting child nodes if any
                            return # Skip this assignment

                    # Rule 2: Exclude empty lists
                    if isinstance(node.value, ast.List):
                        if not node.value.elts: # An empty list has no elements
                            self.generic_visit(node)
                            return # Skip this assignment

                    # Rule 3: Exclude empty dictionaries
                    if isinstance(node.value, ast.Dict):
                        if not node.value.keys: # An empty dict has no keys (and thus no values)
                            self.generic_visit(node)
                            return # Skip this assignment
                    
                    # Rule 4: Exclude single-character variable assignments to integer constants
                    # This targets common loop indices like i = 0, j = 1, etc.
                    if (len(var_name) == 1 and
                            isinstance(node.value, ast.Constant) and
                            isinstance(node.value.value, int)):
                        self.generic_visit(node)
                        return # Skip this assignment

                    # If it passes all exclusion rules, and it's a constant type,
                    # then it's considered hardcoded.
                    self.hardcoded_vars.append((var_name, node.value))
        
        # Ensure that child nodes are visited
        self.generic_visit(node)

def find_hardcoded_variables(code):
    """
    Parses the given Python code and finds hardcoded variable assignments,
    excluding strings that match the '${variable_name}' pattern,
    empty lists, empty dictionaries, and single-character integer assignments.

    Args:
        code (str): The Python script content as a string.

    Returns:
        list: A list of tuples, where each tuple contains the variable name
              and its string representation (e.g., ("API_KEY", '"12345"')).
    """
    # Parse the Python code into an Abstract Syntax Tree (AST)
    tree = ast.parse(code)
    # Initialize the finder
    finder = HardcodedVariableFinder()
    # Visit all nodes in the AST
    finder.visit(tree)

    results = []
    for var_name, value in finder.hardcoded_vars:
        # Get a clean string representation of the value
        if hasattr(ast, "unparse"):
            # ast.unparse is available in Python 3.9+ for a clean representation
            value_repr = ast.unparse(value)
        else:
            # Fallback for older Python versions without ast.unparse
            if isinstance(value, ast.Constant):
                value_repr = repr(value.value)
            elif isinstance(value, ast.Num):
                value_repr = repr(value.n)
            elif isinstance(value, ast.Str):
                value_repr = repr(value.s)
            elif isinstance(value, ast.List):
                # For lists, tuples, and dicts, ast.dump provides a structural representation
                value_repr = ast.dump(value)
            elif isinstance(value, ast.Tuple):
                value_repr = ast.dump(value)
            elif isinstance(value, ast.Dict):
                value_repr = ast.dump(value)
            else:
                value_repr = repr(value) # Generic fallback
        results.append((var_name, value_repr))
    return results