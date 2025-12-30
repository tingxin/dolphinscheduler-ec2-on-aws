#!/usr/bin/env python3
"""
Test script to validate DolphinScheduler configuration fixes
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from src.config import load_config
from src.deploy.config_generator import generate_common_properties_v320

def test_common_properties_generation():
    """Test common.properties generation matches manual.txt format"""
    print("Testing common.properties generation...")
    
    # Load test config
    config = load_config('config.yaml')
    
    # Generate common.properties
    common_props = generate_common_properties_v320(config)
    
    print("Generated common.properties:")
    print("=" * 50)
    print(common_props)
    print("=" * 50)
    
    # Check key elements
    required_elements = [
        'data.basedir.path=/tmp/dolphinscheduler',
        'resource.storage.type=S3',
        'resource.storage.upload.base.path=/dolphinscheduler',
        'resource.aws.access.key.id=',
        'resource.aws.secret.access.key=',
        'resource.aws.region=',
        'resource.aws.s3.bucket.name=',
        'resource.aws.s3.endpoint=',
        'resource.azure.client.id=placeholder',
        'resource.azure.client.secret=placeholder',
        'resource.azure.subId=placeholder',
        'resource.azure.tenant.id=placeholder'
    ]
    
    missing_elements = []
    for element in required_elements:
        if element not in common_props:
            missing_elements.append(element)
    
    if missing_elements:
        print(f"❌ Missing elements: {missing_elements}")
        return False
    else:
        print("✅ All required elements present")
        return True

def test_config_validation():
    """Test configuration validation"""
    print("\nTesting configuration validation...")
    
    try:
        config = load_config('config.yaml')
        print("✅ Configuration loaded successfully")
        
        # Check S3 configuration
        storage_config = config.get('storage', {})
        if storage_config.get('type') == 'S3':
            s3_config = storage_config.get('s3', {})
            required_s3_fields = ['bucket', 'region', 'access_key_id', 'secret_access_key']
            
            missing_s3_fields = []
            for field in required_s3_fields:
                if not s3_config.get(field):
                    missing_s3_fields.append(field)
            
            if missing_s3_fields:
                print(f"⚠️  Missing S3 fields: {missing_s3_fields}")
            else:
                print("✅ S3 configuration complete")
        
        return True
    except Exception as e:
        print(f"❌ Configuration validation failed: {e}")
        return False

def main():
    """Main test function"""
    print("DolphinScheduler Configuration Test")
    print("=" * 40)
    
    tests_passed = 0
    total_tests = 2
    
    # Test 1: common.properties generation
    if test_common_properties_generation():
        tests_passed += 1
    
    # Test 2: config validation
    if test_config_validation():
        tests_passed += 1
    
    print(f"\nTest Results: {tests_passed}/{total_tests} passed")
    
    if tests_passed == total_tests:
        print("🎉 All tests passed! Configuration fixes are working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the configuration.")
        return 1

if __name__ == '__main__':
    sys.exit(main())