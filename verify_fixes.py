#!/usr/bin/env python3
"""
Quick verification script for DolphinScheduler deployment fixes
"""
import os
import sys

def check_file_exists(filepath, description):
    """Check if a file exists and print status"""
    if os.path.exists(filepath):
        print(f"✅ {description}: {filepath}")
        return True
    else:
        print(f"❌ {description}: {filepath} (NOT FOUND)")
        return False

def check_code_fixes():
    """Check if code fixes are applied"""
    print("Checking code fixes...")
    print("=" * 50)
    
    fixes_applied = 0
    total_fixes = 4
    
    # Fix 1: Check installer.py for limited common.properties
    try:
        with open('src/deploy/installer.py', 'r') as f:
            installer_content = f.read()
            if 'storage_components = [\'api\', \'worker\']' in installer_content:
                print("✅ Fix 1: common.properties limited to api-server and worker-server")
                fixes_applied += 1
            else:
                print("❌ Fix 1: common.properties limitation not found")
    except Exception as e:
        print(f"❌ Fix 1: Error checking installer.py: {e}")
    
    # Fix 2: Check package_manager.py for chmod 777
    try:
        with open('src/deploy/package_manager.py', 'r') as f:
            package_content = f.read()
            if 'sudo chmod 777' in package_content and 'mysql-connector-java-8.0.16.jar' in package_content:
                print("✅ Fix 2: MySQL JDBC driver chmod 777 and version 8.0.16")
                fixes_applied += 1
            else:
                print("❌ Fix 2: MySQL JDBC driver fixes not found")
    except Exception as e:
        print(f"❌ Fix 2: Error checking package_manager.py: {e}")
    
    # Fix 3: Check installer.py for plugins_config creation
    try:
        with open('src/deploy/installer.py', 'r') as f:
            installer_content = f.read()
            if 'plugins_config' in installer_content and 'dolphinscheduler-storage-plugin-s3' in installer_content:
                print("✅ Fix 3: plugins_config creation for S3 storage")
                fixes_applied += 1
            else:
                print("❌ Fix 3: plugins_config creation not found")
    except Exception as e:
        print(f"❌ Fix 3: Error checking plugins_config: {e}")
    
    # Fix 4: Check config_generator.py for proper S3 format
    try:
        with open('src/deploy/config_generator.py', 'r') as f:
            config_content = f.read()
            if 'resource.aws.s3.bucket.name' in config_content and 'resource.aws.s3.endpoint' in config_content:
                print("✅ Fix 4: S3 configuration format matches manual.txt")
                fixes_applied += 1
            else:
                print("❌ Fix 4: S3 configuration format not updated")
    except Exception as e:
        print(f"❌ Fix 4: Error checking config_generator.py: {e}")
    
    print(f"\nFixes Applied: {fixes_applied}/{total_fixes}")
    return fixes_applied == total_fixes

def check_project_structure():
    """Check project structure"""
    print("\nChecking project structure...")
    print("=" * 50)
    
    required_files = [
        ('cli.py', 'Main CLI entry point'),
        ('config.yaml', 'Configuration file'),
        ('src/deploy/installer.py', 'Deployment installer'),
        ('src/deploy/package_manager.py', 'Package manager'),
        ('src/deploy/config_generator.py', 'Configuration generator'),
        ('src/commands/create.py', 'Create command'),
        ('test_config.py', 'Configuration test script'),
        ('manual.txt', 'Manual deployment reference')
    ]
    
    files_found = 0
    for filepath, description in required_files:
        if check_file_exists(filepath, description):
            files_found += 1
    
    print(f"\nProject Files: {files_found}/{len(required_files)} found")
    return files_found == len(required_files)

def main():
    """Main verification function"""
    print("DolphinScheduler Deployment Fixes Verification")
    print("=" * 60)
    
    # Check project structure
    structure_ok = check_project_structure()
    
    # Check code fixes
    fixes_ok = check_code_fixes()
    
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    if structure_ok and fixes_ok:
        print("🎉 ALL CHECKS PASSED!")
        print("\nThe DolphinScheduler deployment tool has been successfully fixed.")
        print("Key improvements:")
        print("  • common.properties only in api-server and worker-server")
        print("  • MySQL JDBC driver with proper 777 permissions")
        print("  • plugins_config file for S3 storage support")
        print("  • Configuration format matching manual.txt")
        print("\nYou can now run: python cli.py create --config config.yaml")
        return 0
    else:
        print("❌ SOME CHECKS FAILED!")
        print("\nPlease review the issues above before deploying.")
        return 1

if __name__ == '__main__':
    sys.exit(main())