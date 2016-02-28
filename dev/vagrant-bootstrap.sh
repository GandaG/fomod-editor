#!/usr/bin/env bash

sudo apt-get update

# get git - needed for pyenv

sudo apt-get install -y git
sudo apt-get install -y make build-essential libssl-dev zlib1g-dev libbz2-dev \
 libreadline-dev libsqlite3-dev wget curl llvm libncurses5-dev libncursesw5-dev
sudo apt-get install -y python-dev

# get pyenv - I hate messing with system python on ubuntu

git clone https://github.com/yyuu/pyenv.git /home/vagrant/.pyenv
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> /home/vagrant/.bashrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> /home/vagrant/.bashrc
echo 'eval "$(pyenv init -)"' >> /home/vagrant/.bashrc
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

# get pyenv-virtualenv instead of plain old virtualenv

git clone https://github.com/yyuu/pyenv-virtualenv.git \
 /home/vagrant/.pyenv/plugins/pyenv-virtualenv
echo 'eval "$(pyenv virtualenv-init -)"' >> /home/vagrant/.bashrc
eval "$(pyenv virtualenv-init -)"

# start installing the python versions

pyenv install 2.7.11

# make the virtualenv

pyenv virtualenv 2.7.11 fomod-editor

# install external dependencies

sudo apt-get install -y libxml2-dev libxslt-dev
sudo apt-get install -y qttools5-dev-tools qtcreator python-pyqt5

# link qt to the venv

cp -r /usr/lib/python2.7/dist-packages/PyQt5 \
 /home/vagrant/.pyenv/versions/fomod-editor/lib/python2.7/site-packages/
cp /usr/lib/python2.7/dist-packages/sip* \
 /home/vagrant/.pyenv/versions/fomod-editor/lib/python2.7/site-packages/

# move to the project folder and install the pip reqs

cd /vagrant
pip install -r reqs.txt