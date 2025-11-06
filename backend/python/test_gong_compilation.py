#!/usr/bin/env python3
"""
Test script to verify Gong integration code compiles correctly
"""

import sys
import ast

def test_python_syntax(file_path):
    """Test if a Python file has valid syntax"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Parse the AST to check syntax
        ast.parse(source)
        return True, None
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Error: {e}"

def main():
    """Test all Gong integration files for syntax correctness"""
    files_to_test = [
        "app/sources/client/gong/gong.py",
        "app/sources/external/gong/gong.py", 
        "app/sources/external/gong/example.py"
    ]
    
    print("🧪 Testing Gong integration files for syntax correctness...")
    
    all_passed = True
    for file_path in files_to_test:
        print(f"\n📄 Testing {file_path}...")
        passed, error = test_python_syntax(file_path)
        
        if passed:
            print(f"✅ {file_path}: PASSED")
        else:
            print(f"❌ {file_path}: FAILED - {error}")
            all_passed = False
    
    print(f"\n📊 Results:")
    if all_passed:
        print("✅ All files passed syntax validation!")
        print("🎉 Gong integration code compiles correctly!")
        return True
    else:
        print("❌ Some files failed syntax validation.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)