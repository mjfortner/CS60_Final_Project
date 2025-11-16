#!/usr/bin/env python3

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    try:
        with open('README.md', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "Courier: Delay/Disruption-Tolerant Reliable File Transfer over UDP"

# Read requirements
def read_requirements():
    try:
        with open('requirements.txt', 'r') as f:
            return [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except FileNotFoundError:
        return ['PyYAML>=6.0']

setup(
    name='courier-dtn',
    version='0.1.0',
    description='Delay/Disruption-Tolerant Reliable File Transfer over UDP',
    long_description=read_readme(),
    long_description_content_type='text/markdown',
    author='Maxwell Fortner and Miftah Meky',
    author_email='maxwell.fortner@dartmouth.edu',
    url='https://github.com/maxfortner/courier-dtn',
    
    # Package configuration
    packages=find_packages('src'),
    package_dir={'': 'src'},
    python_requires='>=3.7',
    install_requires=read_requirements(),
    
    # Entry points for command-line scripts
    entry_points={
        'console_scripts': [
            'courier=courier.cli:main',
        ],
    },
    
    # Include additional files
    include_package_data=True,
    package_data={
        'courier': [
            'config/*.yml',
            'config/*.yaml',
        ],
    },
    
    # Classification
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Education',
        'License :: OSI Approved :: MIT License',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS :: MacOS X',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Topic :: Communications :: File Sharing',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: System :: Networking',
    ],
    
    # Additional metadata
    keywords='dtn disruption-tolerant networking udp file-transfer reliability fec',
    project_urls={
        'Bug Reports': 'https://github.com/maxfortner/courier-dtn/issues',
        'Source': 'https://github.com/maxfortner/courier-dtn',
        'Documentation': 'https://github.com/maxfortner/courier-dtn/blob/main/README.md',
    },
    
    # Testing
    extras_require={
        'dev': [
            'pytest>=6.0',
            'pytest-cov>=2.0',
            'black>=21.0',
            'flake8>=3.8',
            'mypy>=0.910',
        ],
        'docker': [
            'docker-compose>=1.29',
        ],
    },
    
    # Zip safety
    zip_safe=False,
)