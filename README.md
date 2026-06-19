# Automation Of Ghost Document Updation
Tool being worked by Aritra Mandal

This project aims to automate the process of updating the documentation for Ghost . It consists of 2 parts, one for updating  the LogFormat Documentation file(`log-format.xml`) and the other for adding or editing an existing QueryTable. It has a web interface built with `Gradio`.
## Environment Variables

To run this project, you will need to add the following environment variables to your .env file in 
the project root.

A Github PAT :

`GITHUB_TOKEN`

## Run Locally

Clone the project

```bash
  git clone https://github.com/armandal-akamai/Ghost-Doc-Update-Automation.git
```

Go to the project directory

```bash
  cd Ghost-Doc-Update-Automation
```

Setup and source the Virtual Environment

```bash
    python3 -m venv venv
    source ./venv/bin/activate
```

Install dependencies

```bash
  python3 -m pip install -r requirements.txt
```

Download the log-format.xml file

```bash
    curl -fsSL https://lp.engr.akamai.com/log-format.xml --output LogFormat/log-format.xml
```
Start the server

```bash
  python3 app.py
```
## Authors

- [@vivekv1993](https://github.com/vivekv1993)
- [@armandal-akamai](https://github.com/armandal-akamai)

