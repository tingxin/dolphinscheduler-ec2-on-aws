from setuptools import setup, find_packages

setup(
    name='dolphinscheduler-ec2-cli',
    version='1.0.0',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'boto3>=1.34.0',
        'click>=8.1.7',
        'PyYAML>=6.0.1',
        'paramiko>=3.4.0',
        'PyMySQL>=1.1.0',
        'kazoo>=2.10.0',
        'python-dotenv>=1.0.0',
        'tqdm>=4.66.0',
        'deepdiff>=6.7.0',
        'colorlog>=6.8.0',
    ],
    entry_points={
        'console_scripts': [
            'ds-cli=cli:cli',
        ],
    },
    python_requires='>=3.12',
    author='DolphinScheduler Team',
    description='DolphinScheduler EC2 Cluster Management CLI',
    long_description=open('DESIGN.md').read(),
    long_description_content_type='text/markdown',
)
