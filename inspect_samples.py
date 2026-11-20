"""读取和盘点本地业务资料。"""

import json
from pathlib import Path


def read_samples():
    """返回以资料名称区分的原始文档。"""
    folder = Path(__file__).resolve().parent / 'samples'
    return {path.stem: json.loads(path.read_text(encoding='utf-8'))
            for path in sorted(folder.glob('*.json'))}


if __name__ == '__main__':
    for name, records in read_samples().items():
        print(f'{name}：{len(records)} 条目')
