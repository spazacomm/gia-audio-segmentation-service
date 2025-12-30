import setuptools

setuptools.setup(
    name='radio-monitoring-pipeline',
    version='0.1.0',
    install_requires=[
        'apache-beam[gcp]==2.61.0',
        'pyacoustid==1.3.0',
        'google-generativeai==0.8.3',
        'pydub==0.25.1',
    ],
    packages=setuptools.find_packages(),
    include_package_data=True,
    description='Radio Monitoring Pipeline on Dataflow',
)
