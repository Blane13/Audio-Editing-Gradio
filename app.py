import gradio as gr
import random
import torch
import torchaudio
from torch import inference_mode
from tempfile import NamedTemporaryFile
import numpy as np
from models import load_model
import utils
from inversion_utils import inversion_forward_process, inversion_reverse_process


def randomize_seed_fn(seed, randomize_seed):
    if randomize_seed:
        seed = random.randint(0, np.iinfo(np.int32).max)
    torch.manual_seed(seed)
    return seed


def invert(x0, prompt_src, num_diffusion_steps, cfg_scale_src):  # , ldm_stable):
    ldm_stable.model.scheduler.set_timesteps(num_diffusion_steps, device=device)

    with inference_mode():
        w0 = ldm_stable.vae_encode(x0)

    # find Zs and wts - forward process
    _, zs, wts = inversion_forward_process(ldm_stable, w0, etas=1,
                                           prompts=[prompt_src],
                                           cfg_scales=[cfg_scale_src],
                                           prog_bar=True,
                                           num_inference_steps=num_diffusion_steps,
                                           numerical_fix=True)
    return zs, wts


def sample(zs, wts, steps, prompt_tar, tstart, cfg_scale_tar):  # , ldm_stable):
    # reverse process (via Zs and wT)
    tstart = torch.tensor(tstart, dtype=torch.int)
    skip = steps - tstart
    w0, _ = inversion_reverse_process(ldm_stable, xT=wts, skips=steps - skip,
                                      etas=1., prompts=[prompt_tar],
                                      neg_prompts=[""], cfg_scales=[cfg_scale_tar],
                                      prog_bar=True,
                                      zs=zs[:int(steps - skip)])

    # vae decode image
    with inference_mode():
        x0_dec = ldm_stable.vae_decode(w0)
    if x0_dec.dim() < 4:
        x0_dec = x0_dec[None, :, :, :]

    with torch.no_grad():
        audio = ldm_stable.decode_to_mel(x0_dec)

    f = NamedTemporaryFile("wb", suffix=".wav", delete=False)
    torchaudio.save(f.name, audio, sample_rate=16000)

    return f.name


def edit(input_audio,
         model_id: str,
         do_inversion: bool,
         wts: gr.State, zs: gr.State, saved_inv_model: str,
         source_prompt="",
         target_prompt="",
         steps=200,
         cfg_scale_src=3.5,
         cfg_scale_tar=12,
         t_start=90,
         randomize_seed=True):

    global ldm_stable, current_loaded_model
    print(f'current loaded model: {ldm_stable.model_id}')
    if model_id != current_loaded_model:
        print(f'Changing model to {model_id}...')
        current_loaded_model = model_id
        ldm_stable = None
        ldm_stable = load_model(model_id, device, steps)

    # If the inversion was done for a different model, we need to re-run the inversion
    if not do_inversion and (saved_inv_model is None or saved_inv_model != model_id):
        do_inversion = True

    x0 = utils.load_audio(input_audio, ldm_stable.get_fn_STFT(), device=device)

    if do_inversion or randomize_seed:  # always re-run inversion
        zs_tensor, wts_tensor = invert(x0=x0, prompt_src=source_prompt,
                                       num_diffusion_steps=steps,
                                       cfg_scale_src=cfg_scale_src)
        wts = gr.State(value=wts_tensor)
        zs = gr.State(value=zs_tensor)
        saved_inv_model = model_id
        do_inversion = False

    output = sample(zs.value, wts.value, steps, prompt_tar=target_prompt, tstart=t_start,
                    cfg_scale_tar=cfg_scale_tar)

    return output, wts, zs, saved_inv_model, do_inversion


current_loaded_model = "cvssp/audioldm2-music"
# current_loaded_model = "cvssp/audioldm2-music"
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
ldm_stable = load_model(current_loaded_model, device, 200)  # deafult model


def get_example():
    case = [
        ['Examples/Beethoven.wav',
         '',
         'A recording of an arcade game soundtrack.',
         90,
         'cvssp/audioldm2-music',
         '27s',
         'Examples/Beethoven_arcade.wav',
         ],
        ['Examples/Beethoven.wav',
         'A high quality recording of wind instruments and strings playing.',
         'A high quality recording of a piano playing.',
         90,
         'cvssp/audioldm2-music',
         '27s',
         'Examples/Beethoven_piano.wav',
         ],
        ['Examples/ModalJazz.wav',
         'Trumpets playing alongside a piano, bass and drums in an upbeat old-timey cool jazz song.',
         'A banjo playing alongside a piano, bass and drums in an upbeat old-timey cool country song.',
         90,
         'cvssp/audioldm2-music',
         '106s',
         'Examples/ModalJazz_banjo.wav',],
        ['Examples/Cat.wav',
         '',
         'A dog barking.',
         150,
         'cvssp/audioldm2-large',
         '10s',
         'Examples/Cat_dog.wav',]
    ]
    return case


intro = """
<h1 style="font-weight: 1400; text-align: center; margin-bottom: 7px;">Zero-Shot Text-Based Audio Editing Using DDPM Inversion</h1>
<h3 style="margin-bottom: 10px; text-align: center;">
    <a href="https://arxiv.org/abs/2402.10009">[Paper]</a>&nbsp;|&nbsp;
    <a href="https://hilamanor.github.io/AudioEditing/">[Project page]</a>&nbsp;|&nbsp;
    <a href="https://github.com/HilaManor/AudioEditingCode">[Code]</a>
</h3>
<p style="font-size:large">
Demo for the text-based editing method introduced in:
<a href="https://arxiv.org/abs/2402.10009" style="text-decoration: underline;" target="_blank">	Zero-Shot Unsupervised and Text-Based Audio Editing Using DDPM Inversion </a> 
</p>
<p style="font-size:larger">
<b>Instructions:</b><br>
Provide an input audio and a target prompt to edit the audio. <br>
T<sub>start</sub> is used to control the tradeoff between fidelity to the original signal and text-adhearance.
Lower value -> favor fidelity. Higher value -> apply a stronger edit.<br>
Make sure that you use an AudioLDM2 version that is suitable for your input audio.
For example, use the music version for music and the large version for general audio.
</p>
<p style="font-size:larger">
You can additionally provide a source prompt to guide even further the editing process.
</p>
<p style="font-size:larger">Longer input will take more time.</p>
<p style="font-size: 0.9rem; margin: 0rem; line-height: 1.2em; margin-top:1em">
For faster inference without waiting in queue, you may duplicate the space and upgrade to GPU in settings.
<a href="https://huggingface.co/spaces/hilamanor/audioEditing?duplicate=true">
<img style="margin-top: 0em; margin-bottom: 0em; display:inline" src="https://bit.ly/3gLdBN6" alt="Duplicate Space" ></a>
</p>

"""

with gr.Blocks(css='style.css') as demo:
    def reset_do_inversion():
        do_inversion = gr.State(value=True)
        return do_inversion

    gr.HTML(intro)
    wts = gr.State()
    zs = gr.State()
    saved_inv_model = gr.State()
    # current_loaded_model = gr.State(value="cvssp/audioldm2-music")
    # ldm_stable = load_model("cvssp/audioldm2-music", device, 200)
    # ldm_stable = gr.State(value=ldm_stable)
    do_inversion = gr.State(value=True)  # To save some runtime when editing the same thing over and over

    with gr.Row():
        with gr.Column():
            src_prompt = gr.Textbox(label="OPTIONAL: Source Prompt", lines=2, interactive=True,
                                    placeholder="Optional: Describe the original audio input",)
            input_audio = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Input Audio",
                                   interactive=True, scale=1)

        with gr.Column():
            tar_prompt = gr.Textbox(label="Target Prompt", placeholder="Describe your desired edited output",
                                    lines=2, interactive=True)
            output_audio = gr.Audio(label="Edited Audio", interactive=False, scale=1)

    with gr.Row():
        with gr.Column():
            submit = gr.Button("Edit")

    with gr.Row():
        t_start = gr.Slider(minimum=30, maximum=160, value=110, step=1, label="T-start", interactive=True, scale=3,
                            info="Higher T-start -> stronger edit. Lower T-start -> more similar to original audio.")
        model_id = gr.Dropdown(label="AudioLDM2 Version", choices=["cvssp/audioldm2",
                                                                   "cvssp/audioldm2-large",
                                                                   "cvssp/audioldm2-music"],
                               info="Choose a checkpoint suitable for your intended audio and edit.",
                               value="cvssp/audioldm2-music", interactive=True, type="value", scale=2)
    with gr.Accordion("More Options", open=False):

        with gr.Row():
            cfg_scale_src = gr.Number(value=3, minimum=0.5, maximum=25, precision=None,
                                      label="Source Guidance Scale", interactive=True, scale=1)
            cfg_scale_tar = gr.Number(value=12, minimum=0.5, maximum=25, precision=None,
                                      label="Target Guidance Scale", interactive=True, scale=1)
            steps = gr.Number(value=200, precision=0, minimum=20, maximum=1000,
                              label="Num Diffusion Steps", interactive=True, scale=1)
        with gr.Row():
            seed = gr.Number(value=0, precision=0, label="Seed", interactive=True)
            randomize_seed = gr.Checkbox(label='Randomize seed', value=False)
            length = gr.Number(label="Length", interactive=False, visible=False)

    def change_tstart_range(steps):
        t_start.maximum = int(160/200 * steps)
        t_start.minimum = int(30/200 * steps)
        if t_start.value > t_start.maximum:
            t_start.value = t_start.maximum
        if t_start.value < t_start.minimum:
            t_start.value = t_start.minimum
        return t_start

    submit.click(
        fn=randomize_seed_fn,
        inputs=[seed, randomize_seed],
        outputs=[seed], queue=False).then(
           fn=edit,
           inputs=[input_audio,
                   model_id,
                   do_inversion,
                   #    current_loaded_model, ldm_stable,
                   wts, zs, saved_inv_model,
                   src_prompt,
                   tar_prompt,
                   steps,
                   cfg_scale_src,
                   cfg_scale_tar,
                   t_start,
                   randomize_seed
                   ],
           outputs=[output_audio, wts, zs, saved_inv_model, do_inversion]  # , current_loaded_model, ldm_stable],
        )

    # If sources changed we have to rerun inversion
    input_audio.change(fn=reset_do_inversion, outputs=[do_inversion])
    src_prompt.change(fn=reset_do_inversion, outputs=[do_inversion])
    model_id.change(fn=reset_do_inversion, outputs=[do_inversion])
    steps.change(fn=change_tstart_range, inputs=[steps], outputs=[t_start])

    gr.Examples(
        label="Examples",
        examples=get_example(),
        inputs=[input_audio, src_prompt, tar_prompt, t_start, model_id, length, output_audio],
        outputs=[output_audio]
    )

    demo.queue()
    demo.launch()