import numpy as np
import os
import torch
import torch.nn.functional as F
from omegaconf.omegaconf import open_dict
from pytorch_lightning.trainer.trainer import Trainer
from torch import nn
import numpy as np
import matplotlib.pyplot as plt
from nemo.core.config import hydra_runner

from nemo.collections.nlp.models.language_modeling.megatron_gpt_prompt_learning_model import (
    MegatronGPTPromptLearningModel,
)
from nemo.collections.nlp.parts.nlp_overrides import NLPDDPStrategy

def load_prompt_model(virtual_prompt_model_file):
    d = {"devices": 1, "num_nodes": 1, "accelerator": "gpu", "logger": False, "precision": 16}
    trainer = Trainer(strategy=NLPDDPStrategy(), **d)
    prompt_learning_cfg = MegatronGPTPromptLearningModel.restore_from(
        virtual_prompt_model_file, trainer=trainer, return_config=True,
    )

    with open_dict(prompt_learning_cfg):
        prompt_learning_cfg.save_nemo_on_validation_end = False
        prompt_learning_cfg.micro_batch_size = 1
        prompt_learning_cfg.global_batch_size = 1
        if prompt_learning_cfg.language_model_path.startswith("/home/adithyare"):
            pass
        else:
            prompt_learning_cfg.language_model_path = "/home/adithyare" + prompt_learning_cfg.language_model_path


    model = MegatronGPTPromptLearningModel.restore_from(
        restore_path=virtual_prompt_model_file, trainer=trainer, override_config_path=prompt_learning_cfg,
    )
    return model

def norm(embs):
    print(torch.norm(embs, dim=1, keepdim=True))
    return embs / torch.norm(embs, dim=1, keepdim=True)

@hydra_runner(config_path="./", config_name="viz")
def main(cfg):
    prompt_model_file = cfg.viz.prompt_model_file
    model = load_prompt_model(prompt_model_file)

    prompts =  model.prompt_table.prompt_table["squad"].prompt_embeddings.weight.data
    prompt_norm = norm(prompts) # 10 X 768
    cs_mat = prompt_norm @ prompt_norm.transpose(0, 1)


    plt.imshow(cs_mat.cpu().numpy())
    plt.colorbar()
    plt.savefig(cfg.viz.viz_file)
    print(f'saved to:{cfg.viz.viz_file}')



if __name__ == '__main__':
    main()
