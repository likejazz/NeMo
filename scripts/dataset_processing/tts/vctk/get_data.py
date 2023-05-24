# Copyright (c) 2023, NVIDIA CORPORATION & AFFILIATES.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import glob
import json
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

from tqdm import tqdm

from nemo.utils import logging


DATASET_URL = "https://datashare.ed.ac.uk/bitstream/handle/10283/2651/VCTK-Corpus.zip?sequence=2&isAllowed=y"


def get_args():
    parser = argparse.ArgumentParser(description='Download VCTK and create manifests')
    parser.add_argument(
        "--data_dir",
        required=True,
        type=Path,
        help='Directory into which to download and extract dataset.',
    )
    args = parser.parse_args()
    return args


def _maybe_download_file(destination_path):
    if not destination_path.exists():
        print(destination_path)
        tmp_file_path = destination_path.with_suffix('.tmp')
        urllib.request.urlretrieve(DATASET_URL, filename=str(tmp_file_path))
        tmp_file_path.rename(destination_path)
    else:
        logging.info(f"Skipped downloading data because it exists: {destination_path}")


def _extract_file(filepath, data_dir):
    logging.info(f"Unzipping data: {filepath} --> {data_dir}")
    shutil.unpack_archive(filepath, data_dir)
    logging.info(f"Unzipping data is complete: {filepath}.")

'''
def _process_data(data_root, filelists):
    # Create manifests (based on predefined NVIDIA's split)
    for split in tqdm(filelists):
        manifest_target = data_root / f"{split}_manifest.json"
        print(f"Creating manifest for {split}.")

        entries = []
        for manifest_src in glob.glob(str(data_root / f"*_{split}.json")):
            try:
                search_res = re.search('.*\/([0-9]+)_manifest_([a-z]+)_.*.json', manifest_src)
                speaker_id = search_res.group(1)
                audio_quality = search_res.group(2)
            except Exception:
                print(f"Failed to find speaker id or audio quality for {manifest_src}, check formatting.")
                continue

            with open(manifest_src, 'r') as f_in:
                for input_json_entry in f_in:
                    data = json.loads(input_json_entry)

                    # Make sure corresponding wavfile exists
                    wav_path = data_root / data['audio_filepath']
                    assert wav_path.exists(), f"{wav_path} does not exist!"

                    entry = {
                        'audio_filepath': data['audio_filepath'],
                        'duration': data['duration'],
                        'text': data['text'],
                        'normalized_text': data['text_normalized'],
                        'speaker': int(speaker_id),
                        # Audio_quality is either clean or other.
                        # The clean set includes recordings with high sound-to-noise ratio and wide bandwidth.
                        # The books with noticeable noise or narrow bandwidth are included in the other subset.
                        # Note: some speaker_id's have both clean and other audio quality.
                        'audio_quality': audio_quality,
                    }
                    entries.append(entry)

        with open(manifest_target, 'w') as f_out:
            for m in entries:
                f_out.write(json.dumps(m) + '\n')
'''


def main():
    args = get_args()
    data_dir = args.data_dir

    tarred_filepath = data_dir / "vctk.tar.gz"
    _maybe_download_file(tarred_filepath)
    #_extract_file(tarred_filepath, args.data_root)

    #data_root = args.data_root / "hi_fi_tts_v0"
    #_process_data(data_root, split)


if __name__ == '__main__':
    main()
