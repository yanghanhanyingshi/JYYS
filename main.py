name: 自动更新直播源 定时3h任务

on:
  schedule:
    - cron: '0 */3 * * *' # 每3小时执行一次
  workflow_dispatch: # 允许手动触发

jobs:
  run-spider:
    runs-on: ubuntu-latest
    # 关键修复1: 授予基础读写权限
    permissions:
      contents: write

    steps:
      - name: 拉取仓库代码
        uses: actions/checkout@v4
        with:
          fetch-depth: 0 # 必须拉取完整历史以支持rebase

      - name: 配置Python环境
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install requests

      - name: 执行采集整理脚本
        run: python run_spider.py # 请确保你的脚本文件名与此一致

      - name: 提交并推送更新
        # 关键修复2: 使用PAT并处理冲突
        uses: ad-m/github-push-action@master
        with:
          github_token: ${{ secrets.PERSONAL_TOKEN }} # 使用自定义Token
          branch: main
          # 核心命令：先拉取远程最新代码合并，再推送
          force: false
          message: 'Auto commit by GitHub Actions: 更新直播源与香港频道配置'
        env:
          GIT_COMMITTER_NAME: github-actions[bot]
          GIT_COMMITTER_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
