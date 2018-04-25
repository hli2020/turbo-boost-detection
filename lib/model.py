import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from lib.layers import pyramid_roi_align


class SamePad2d(nn.Module):
    """
        Mimic tensorflow's 'SAME' padding.
    """

    def __init__(self, kernel_size, stride):
        super(SamePad2d, self).__init__()
        self.kernel_size = torch.nn.modules.utils._pair(kernel_size)
        self.stride = torch.nn.modules.utils._pair(stride)

    def forward(self, input):
        in_width = input.size()[2]
        in_height = input.size()[3]
        out_width = math.ceil(float(in_width) / float(self.stride[0]))
        out_height = math.ceil(float(in_height) / float(self.stride[1]))
        pad_along_width = ((out_width - 1) * self.stride[0] +
                           self.kernel_size[0] - in_width)
        pad_along_height = ((out_height - 1) * self.stride[1] +
                            self.kernel_size[1] - in_height)
        pad_left = math.floor(pad_along_width / 2)
        pad_top = math.floor(pad_along_height / 2)
        pad_right = pad_along_width - pad_left
        pad_bottom = pad_along_height - pad_top
        return F.pad(input, (pad_left, pad_right, pad_top, pad_bottom), 'constant', 0)

    def __repr__(self):
        return self.__class__.__name__


############################################################
#  FPN Graph
############################################################
# not used
# class TopDownLayer(nn.Module):
#
#     def __init__(self, in_channels, out_channels):
#         super(TopDownLayer, self).__init__()
#         self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=1)
#         self.padding2 = SamePad2d(kernel_size=3, stride=1)
#         self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=1)
#
#     def forward(self, x, y):
#         y = F.upsample(y, scale_factor=2)
#         x = self.conv1(x)
#         return self.conv2(self.padding2(x+y))
class FPN(nn.Module):
    def __init__(self, C1, C2, C3, C4, C5, out_channels):
        super(FPN, self).__init__()
        self.out_channels = out_channels
        self.C1 = C1
        self.C2 = C2
        self.C3 = C3
        self.C4 = C4
        self.C5 = C5
        self.P6 = nn.MaxPool2d(kernel_size=1, stride=2)
        self.P5_conv1 = nn.Conv2d(2048, self.out_channels, kernel_size=1, stride=1)
        self.P5_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )
        self.P4_conv1 =  nn.Conv2d(1024, self.out_channels, kernel_size=1, stride=1)
        self.P4_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )
        self.P3_conv1 = nn.Conv2d(512, self.out_channels, kernel_size=1, stride=1)
        self.P3_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )
        self.P2_conv1 = nn.Conv2d(256, self.out_channels, kernel_size=1, stride=1)
        self.P2_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )

    def forward(self, x):
        x = self.C1(x)
        x = self.C2(x)
        c2_out = x
        x = self.C3(x)
        c3_out = x
        x = self.C4(x)
        c4_out = x
        x = self.C5(x)
        p5_out = self.P5_conv1(x)
        p4_out = self.P4_conv1(c4_out) + F.upsample(p5_out, scale_factor=2)
        p3_out = self.P3_conv1(c3_out) + F.upsample(p4_out, scale_factor=2)
        p2_out = self.P2_conv1(c2_out) + F.upsample(p3_out, scale_factor=2)

        p5_out = self.P5_conv2(p5_out)
        p4_out = self.P4_conv2(p4_out)
        p3_out = self.P3_conv2(p3_out)
        p2_out = self.P2_conv2(p2_out)

        # P6 is used for the 5th anchor scale in RPN. Generated by
        # subsampling from P5 with stride of 2.
        p6_out = self.P6(p5_out)

        return [p2_out, p3_out, p4_out, p5_out, p6_out]


############################################################
#  Resnet Graph
############################################################
class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, stride=stride)
        self.bn1 = nn.BatchNorm2d(planes, eps=0.001, momentum=0.01)
        self.padding2 = SamePad2d(kernel_size=3, stride=1)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3)
        self.bn2 = nn.BatchNorm2d(planes, eps=0.001, momentum=0.01)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1)
        self.bn3 = nn.BatchNorm2d(planes * 4, eps=0.001, momentum=0.01)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.padding2(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self, architecture, stage5=False):
        super(ResNet, self).__init__()
        assert architecture in ["resnet50", "resnet101"]
        self.inplanes = 64
        self.layers = [3, 4, {"resnet50": 6, "resnet101": 23}[architecture], 3]
        self.block = Bottleneck
        self.stage5 = stage5

        self.C1 = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3),
            nn.BatchNorm2d(64, eps=0.001, momentum=0.01),
            nn.ReLU(inplace=True),
            SamePad2d(kernel_size=3, stride=2),
            nn.MaxPool2d(kernel_size=3, stride=2),
        )
        self.C2 = self.make_layer(self.block, 64, self.layers[0])
        self.C3 = self.make_layer(self.block, 128, self.layers[1], stride=2)
        self.C4 = self.make_layer(self.block, 256, self.layers[2], stride=2)
        if self.stage5:
            self.C5 = self.make_layer(self.block, 512, self.layers[3], stride=2)
        else:
<<<<<<< HEAD
            self.C5 = None

    def forward(self, x):
        x = self.C1(x)
        x = self.C2(x)
        x = self.C3(x)
        x = self.C4(x)
        x = self.C5(x)
        return x

    def stages(self):
        return [self.C1, self.C2, self.C3, self.C4, self.C5]

    def make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride),
                nn.BatchNorm2d(planes * block.expansion, eps=0.001, momentum=0.01),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)


############################################################
#  Region Proposal Network
############################################################
class RPN(nn.Module):
    """Builds the model of Region Proposal Network.
    anchors_per_location: number of anchors per pixel in the feature map
    anchor_stride: Controls the density of anchors. Typically 1 (anchors for
                   every pixel in the feature map), or 2 (every other pixel).
    Returns:
        rpn_logits: [batch, H, W, 2] Anchor classifier logits (before softmax)
        rpn_probs: [batch, W, W, 2] Anchor classifier probabilities.
        rpn_bbox: [batch, H, W, (dy, dx, log(dh), log(dw))] Deltas to be
                  applied to anchors.
=======
            loss = train_epoch_new(input_model, train_generator, optimizer,
                                   stage_name=stage_name, epoch_str=epoch_str,
                                   epoch=epoch, start_iter=model.iter+1)
        # Validation
        # val_loss = valid_epoch(val_generator, model.config.VALIDATION_STEPS)

        # Statistics
        model.loss_history.append(loss)
        # model.val_loss_history.append(val_loss)
        visualize.plot_loss(model.loss_history, model.val_loss_history, save=True, log_dir=model.log_dir)
        model_file = model.checkpoint_path.format(epoch)
        print_log('saving model: {:s}\n'.format(model_file), model.config.LOG_FILE)
        torch.save({'state_dict': model.state_dict()}, model_file)
        model.iter = 0
        model.epoch = epoch


def train_epoch_new(input_model, data_loader, optimizer, **args):
    """new training flow scheme"""
    if isinstance(input_model, nn.DataParallel):
        model = input_model.module
    else:
        # single-gpu
        model = input_model

    loss_sum = 0
    config = model.config
    data_iterator = iter(data_loader)
    iter_per_epoch = math.ceil(len(data_loader)/config.BATCH_SIZE)
    save_iter_base = math.floor(iter_per_epoch / config.SAVE_TIME_WITHIN_EPOCH)

    for iter_ind in range(args['start_iter'], iter_per_epoch+1):

        inputs = next(data_iterator)

        images = Variable(inputs[0].cuda())
        target_rpn_match = Variable(inputs[2].cuda())
        target_rpn_bbox = Variable(inputs[3].cuda())
        # pad with zeros
        gt_class_ids, gt_boxes, gt_masks, _ = model.adjust_input_gt(inputs[4], inputs[5], inputs[6])

        # Run object detection
        # [rpn_class_logits, rpn_pred_bbox,
        # target_class_ids, mrcnn_class_logits, target_deltas, mrcnn_bbox, target_mask, mrcnn_mask]
        outputs = input_model([images, gt_class_ids, gt_boxes, gt_masks], mode=model.config.PHASE)

        # Compute losses
        loss, detailed_losses = compute_loss(target_rpn_match, target_rpn_bbox, outputs)

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm(input_model.parameters(), 5.0)
        optimizer.step()

        # Progress
        if iter_ind % model.config.SHOW_INTERVAL == 0 or iter_ind == args['start_iter']:
            print_log('[{:s}][stage {:s}]{:s}\t{}/{}\tloss: {:.5f} - rpn_cls: {:.5f} - rpn_bbox: {:.5f} '
                      '- mrcnn_cls: {:.5f} - mrcnn_bbox: {:.5f} - mrcnn_mask_loss: {:.5f}'.
                      format(model.config.NAME, args['stage_name'], args['epoch_str'], iter_ind+1, iter_per_epoch,
                             loss.data.cpu()[0],
                             detailed_losses[0].data.cpu()[0],
                             detailed_losses[1].data.cpu()[0],
                             detailed_losses[2].data.cpu()[0],
                             detailed_losses[3].data.cpu()[0],
                             detailed_losses[4].data.cpu()[0]), config.LOG_FILE)
        # Statistics
        loss_sum += loss.data.cpu()[0]/iter_per_epoch
        if iter_ind % save_iter_base == 0:
            model_file = os.path.join(model.log_dir,
                                      'mask_rcnn_{:04d}_iter_{:d}.pth'.format(args['epoch'], iter_ind))
            print_log('saving model file to: {:s}'.format(model_file), config.LOG_FILE)
            torch.save({
                'state_dict':   model.state_dict(),
                'epoch':        model.epoch,
                'iter':         iter_ind,
            }, model_file)

    return loss_sum


def test_model(input_model, valset, coco_api, limit=-1, image_ids=None):
    """
        Test the trained model
        Args:
            input_model:    nn.DataParallel
            valset:         validation dataset
            coco_api:       api
            limit:          the number of images to use for evaluation
            image_ids:      a certain image
    """
    if isinstance(input_model, nn.DataParallel):
        model = input_model.module
    else:
        # single-gpu
        model = input_model

    model_file_name = os.path.basename(model.config.START_MODEL_FILE)
    dataset = valset.dataset

    # Pick COCO images from the dataset
    image_ids = image_ids or dataset.image_ids
    # Limit to a subset
    if limit > 0:
        image_ids = image_ids[:limit]

    num_test_im = len(image_ids)
    print("Running COCO evaluation on {} images.".format(num_test_im))
    assert (num_test_im % model.config.BATCH_SIZE) % model.config.GPU_COUNT == 0, 'last mini-batch in an epoch' \
                                                                                  'is not divisible by gpu number.'
    # Get corresponding COCO image IDs.
    coco_image_ids = [dataset.image_info[ind]["id"] for ind in image_ids]

    t_prediction = 0
    t_start = time.time()

    results = []
    total_iter = math.ceil(num_test_im / model.config.BATCH_SIZE)
    cnt = 0

    # for i, image_id in enumerate(image_ids):
    for iter_ind in range(total_iter):
        curr_image_ids = image_ids[iter_ind*model.config.BATCH_SIZE :
                            min(iter_ind*model.config.BATCH_SIZE + model.config.BATCH_SIZE, num_test_im)]

        # Run detection
        t_pred_start = time.time()
        # Mold inputs to format expected by the neural network
        molded_images, image_metas, windows, images = _mold_inputs(model, curr_image_ids, dataset)

        # Run object detection; detections: 8,100,6; mrcnn_mask: 8,100,81,28,28
        detections, mrcnn_mask = input_model([molded_images, image_metas], mode=model.config.PHASE)

        # Convert to numpy
        detections = detections.data.cpu().numpy()
        mrcnn_mask = mrcnn_mask.permute(0, 1, 3, 4, 2).data.cpu().numpy()

        # Process detections
        for i, image in enumerate(images):

            curr_coco_id = coco_image_ids[curr_image_ids[i]]
            final_rois, final_class_ids, final_scores, final_masks = _unmold_detections(
                detections[i], mrcnn_mask[i], image.shape, windows[i])

            if final_rois is None:
                continue
            for det_id in range(final_rois.shape[0]):
                bbox = np.around(final_rois[det_id], 1)
                curr_result = {
                    "image_id":     curr_coco_id,
                    "category_id":  dataset.get_source_class_id(final_class_ids[det_id], "coco"),
                    "bbox":         [bbox[1], bbox[0], bbox[3] - bbox[1], bbox[2] - bbox[0]],
                    "score":        final_scores[det_id],
                    "segmentation": maskUtils.encode(np.asfortranarray(final_masks[:, :, det_id]))
                }
                results.append(curr_result)

            # visualize result if necessary
            #if model.config.DEBUG:
<<<<<<< HEAD
            #    plt.close()
            #    visualize.display_instances(image, final_rois, final_masks, final_class_ids,
            #                                CLASS_NAMES, final_scores)
            #    im_file = os.path.join(model.config.SAVE_IMAGE_DIR,
            #                           'coco_im_id_{:d}.png'.format(curr_coco_id))
            #    plt.savefig(im_file)
=======
            #     plt.close()
            #     visualize.display_instances(image, final_rois, final_masks, final_class_ids,
            #                                 CLASS_NAMES, final_scores)
            #     im_file = os.path.join(model.config.SAVE_IMAGE_DIR,
            #                            'coco_im_id_{:d}.png'.format(curr_coco_id))
            #     plt.savefig(im_file)
>>>>>>> 9b90a30ce80fefff1a893f0a99d6b341bca0d809

        t_prediction += (time.time() - t_pred_start)
        cnt += len(curr_image_ids)
        if iter_ind % (model.config.SHOW_INTERVAL*10) == 0 or cnt == len(image_ids):
            print_log('[{:s}][{:s}] evaluation progress \t{:4d} images /{:4d} total ...'.
                      format(model.config.NAME, model_file_name, cnt, len(image_ids)), model.config.LOG_FILE)

    print_log("Prediction time: {:.4f}. Average {:.4f} sec/image".format(
        t_prediction, t_prediction / len(image_ids)), model.config.LOG_FILE)
    print_log('Saving results to {:s}'.format(model.config.RESULT_FILE), model.config.LOG_FILE)
    torch.save({'det_result': results}, model.config.RESULT_FILE)

    # Evaluate
    print('\nBegin to evaluate ...')
    # Load results. This modifies results with additional attributes.
    coco_results = coco_api.loadRes(results)
    eval_type = "bbox"
    cocoEval = COCOeval(coco_api, coco_results, eval_type)
    cocoEval.params.imgIds = coco_image_ids
    cocoEval.evaluate()
    cocoEval.accumulate()
    cocoEval.summarize()
    print_log('Total time: {:.4f}'.format(time.time() - t_start), model.config.LOG_FILE)
<<<<<<< HEAD
    print_log('config [{:s}], model file [{:s}], mAP is {:.4f}\n\n'.
              format(model.config.NAME, os.path.basename(model.config.START_MODEL_FILE), cocoEval.stats[0]),
              model.config.LOG_FILE)
=======
    print_log('config [{:s}], model file [{:s}], mAP is {:.4f}\n\n'.format(
              model.config.NAME, os.path.basename(model.config.START_MODEL_FILE), model.config.LOG_FILE)
>>>>>>> 9b90a30ce80fefff1a893f0a99d6b341bca0d809


def compute_loss(target_rpn_match, target_rpn_bbox, inputs):

    rpn_class_logits, rpn_pred_bbox, target_class_ids, \
        mrcnn_class_logits, target_deltas, mrcnn_bbox, target_mask, mrcnn_mask = \
        inputs[0], inputs[1], inputs[2], inputs[3], inputs[4], inputs[5], inputs[6], inputs[7]

    rpn_class_loss = compute_rpn_class_loss(target_rpn_match, rpn_class_logits)
    rpn_bbox_loss = compute_rpn_bbox_loss(target_rpn_bbox, target_rpn_match, rpn_pred_bbox)
    mrcnn_class_loss = compute_mrcnn_class_loss(target_class_ids, mrcnn_class_logits)
    mrcnn_bbox_loss = compute_mrcnn_bbox_loss(target_deltas, target_class_ids, mrcnn_bbox)
    mrcnn_mask_loss = compute_mrcnn_mask_loss(target_mask, target_class_ids, mrcnn_mask)

    outputs = [rpn_class_loss, rpn_bbox_loss, mrcnn_class_loss, mrcnn_bbox_loss, mrcnn_mask_loss]
    return sum(outputs), outputs


def train_epoch(model, datagenerator, optimizer, steps, stage_name, epoch_str):
    batch_count, loss_sum, step = 0, 0, 0

    for inputs in datagenerator:

        batch_count += 1

        images = Variable(inputs[0]).cuda()
        image_metas = inputs[1].numpy()
        gt_class_ids = inputs[4]
        gt_boxes = inputs[5]
        gt_masks = inputs[6]

        # Run object detection
        outputs = \
            model([images, image_metas, gt_class_ids, gt_boxes, gt_masks], mode='training')

        # Compute losses
        rpn_match = Variable(inputs[2]).cuda()
        rpn_bbox = Variable(inputs[3]).cuda()
        loss, detailed_losses = compute_loss(rpn_match, rpn_bbox, outputs)

        # backprop
        if (batch_count % model.config.BATCH_SIZE) == 0:
            optimizer.zero_grad()
        # TODO: no average here?
        loss.backward()
        torch.nn.utils.clip_grad_norm(model.parameters(), 5.0)
        if (batch_count % model.config.BATCH_SIZE) == 0:
            optimizer.step()
            batch_count = 0

        # Progress
        if step % model.config.SHOW_INTERVAL == 0:
            print_log('[{:s}][stage {:s}]{:s}\t{}/{}\tloss: {:.5f} - rpn_cls: {:.5f} - rpn_bbox: {:.5f} '
                      '- mrcnn_cls: {:.5f} - mrcnn_bbox: {:.5f} - mrcnn_mask_loss: {:.5f}'.
                      format(model.config.NAME, stage_name, epoch_str, step+1, steps,
                             loss.data.cpu()[0],
                             detailed_losses[0].data.cpu()[0],
                             detailed_losses[1].data.cpu()[0],
                             detailed_losses[2].data.cpu()[0],
                             detailed_losses[3].data.cpu()[0],
                             detailed_losses[4].data.cpu()[0]), model.config.LOG_FILE)
        # Statistics
        loss_sum += loss.data.cpu()[0]/steps

        # Break after 'steps' steps
        # TODO: default steps - 16000; hence each epoch has the same first 16000 images to train?
        if step == steps-1:
            break
        step += 1
    return loss_sum


def _mold_inputs(model, image_ids, dataset):
    """
        FOR EVALUATION ONLY.
        Takes a list of images and modifies them to the format expected as an input to the neural network.
        images: List of image matrices [height,width,depth]. Images can have different sizes.

        Returns 3 Numpy matrices:
            molded_images: [N, h, w, 3]. Images resized and normalized.
            image_metas: [N, length of meta datasets]. Details about each image.
            windows: [N, (y1, x1, y2, x2)]. The portion of the image that has the
            original image (padding excluded).
    """
    molded_images = []
    image_metas = []
    windows = []
    images = []

    for curr_id in image_ids:
        image = dataset.load_image(curr_id)
        # Resize image to fit the model expected size
        molded_image, window, scale, padding = utils.resize_image(
            image,
            min_dim=model.config.IMAGE_MIN_DIM,
            max_dim=model.config.IMAGE_MAX_DIM,
            padding=model.config.IMAGE_PADDING)
        molded_image = utils.mold_image(molded_image, model.config)
        # Build image_meta
        image_meta = utils.compose_image_meta(
            0, image.shape, window,
            np.zeros([model.config.NUM_CLASSES], dtype=np.int32))
        # Append
        molded_images.append(molded_image)
        windows.append(window)
        image_metas.append(image_meta)
        images.append(image)
    # Pack into arrays
    molded_images = np.stack(molded_images)
    image_metas = np.stack(image_metas)
    windows = np.stack(windows)

    # Convert images to torch tensor
    molded_images = torch.from_numpy(molded_images.transpose(0, 3, 1, 2)).float()
    molded_images = Variable(molded_images.cuda(), volatile=True)

    return molded_images, image_metas, windows, images


def _unmold_detections(detections, mrcnn_mask, image_shape, window):
    """
        FOR EVALUATION ONLY.
        Re-formats the detections of one image from the format of the neural
        network output to a format suitable for use in the rest of the application.

            detections:     [N, (y1, x1, y2, x2, class_id, score)]
            mrcnn_mask:     [N, height, width, num_classes]
            image_shape:    [height, width, depth] Original size of the image before resizing
            window:         [y1, x1, y2, x2] Box in the image where the real image is excluding the padding.

        Returns:
            boxes:          [N, (y1, x1, y2, x2)] Bounding boxes in pixels
            class_ids:      [N] Integer class IDs for each bounding box
            scores:         [N] Float probability scores of the class_id
            masks:          [height, width, num_instances] Instance masks
>>>>>>> e24c86c5aa72b69cf0f4172a80bef9fabf4e3051
    """

    def __init__(self, anchors_per_location, anchor_stride, depth):
        super(RPN, self).__init__()
        self.anchors_per_location = anchors_per_location
        self.anchor_stride = anchor_stride
        self.depth = depth

        self.padding = SamePad2d(kernel_size=3, stride=self.anchor_stride)
        self.conv_shared = nn.Conv2d(self.depth, 512, kernel_size=3, stride=self.anchor_stride)
        self.relu = nn.ReLU(inplace=True)
        self.conv_class = nn.Conv2d(512, 2 * anchors_per_location, kernel_size=1, stride=1)
        self.softmax = nn.Softmax(dim=2)
        self.conv_bbox = nn.Conv2d(512, 4 * anchors_per_location, kernel_size=1, stride=1)

    def forward(self, x):
        # Shared convolutional base of the RPN
        x = self.relu(self.conv_shared(self.padding(x)))

        # Anchor Score. [batch, anchors per location * 2, height, width].
        rpn_class_logits = self.conv_class(x)

        # Reshape to [batch, 2, anchors]
        rpn_class_logits = rpn_class_logits.permute(0, 2, 3, 1)
        rpn_class_logits = rpn_class_logits.contiguous()
        rpn_class_logits = rpn_class_logits.view(x.size()[0], -1, 2)

        # Softmax on last dimension of BG/FG.
        rpn_probs = self.softmax(rpn_class_logits)

        # Bounding box refinement. [batch, H, W, anchors per location, depth]
        # where depth is [x, y, log(w), log(h)]
        rpn_bbox = self.conv_bbox(x)

        # Reshape to [batch, 4, anchors]
        rpn_bbox = rpn_bbox.permute(0, 2, 3, 1)
        rpn_bbox = rpn_bbox.contiguous()
        rpn_bbox = rpn_bbox.view(x.size()[0], -1, 4)

        return [rpn_class_logits, rpn_probs, rpn_bbox]


############################################################
#  Feature Pyramid Network Heads
############################################################
class Classifier(nn.Module):
    def __init__(self, depth, pool_size, image_shape, num_classes):
        super(Classifier, self).__init__()
        self.depth = depth
        self.pool_size = pool_size
        self.image_shape = image_shape
        self.num_classes = num_classes
        self.conv1 = nn.Conv2d(self.depth, 1024, kernel_size=self.pool_size, stride=1)
        self.bn1 = nn.BatchNorm2d(1024, eps=0.001, momentum=0.01)
        self.conv2 = nn.Conv2d(1024, 1024, kernel_size=1, stride=1)
        self.bn2 = nn.BatchNorm2d(1024, eps=0.001, momentum=0.01)
        self.relu = nn.ReLU(inplace=True)

        self.linear_class = nn.Linear(1024, num_classes)
        self.softmax = nn.Softmax(dim=1)

        self.linear_bbox = nn.Linear(1024, num_classes * 4)

    def forward(self, x, rois):
        x = pyramid_roi_align([rois] + x, self.pool_size, self.image_shape)
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu(x)

        x = x.view(-1, 1024)
        mrcnn_class_logits = self.linear_class(x)
        mrcnn_probs = self.softmax(mrcnn_class_logits)

        mrcnn_bbox = self.linear_bbox(x)
        mrcnn_bbox = mrcnn_bbox.view(mrcnn_bbox.size()[0], -1, 4)

        return [mrcnn_class_logits, mrcnn_probs, mrcnn_bbox]


class Mask(nn.Module):
    def __init__(self, depth, pool_size, image_shape, num_classes):
        super(Mask, self).__init__()
        self.depth = depth
        self.pool_size = pool_size
        self.image_shape = image_shape
        self.num_classes = num_classes
        self.padding = SamePad2d(kernel_size=3, stride=1)
        self.conv1 = nn.Conv2d(self.depth, 256, kernel_size=3, stride=1)
        self.bn1 = nn.BatchNorm2d(256, eps=0.001)
        self.conv2 = nn.Conv2d(256, 256, kernel_size=3, stride=1)
        self.bn2 = nn.BatchNorm2d(256, eps=0.001)
        self.conv3 = nn.Conv2d(256, 256, kernel_size=3, stride=1)
        self.bn3 = nn.BatchNorm2d(256, eps=0.001)
        self.conv4 = nn.Conv2d(256, 256, kernel_size=3, stride=1)
        self.bn4 = nn.BatchNorm2d(256, eps=0.001)
        self.deconv = nn.ConvTranspose2d(256, 256, kernel_size=2, stride=2)
        self.conv5 = nn.Conv2d(256, num_classes, kernel_size=1, stride=1)
        self.sigmoid = nn.Sigmoid()
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x, rois):
        x = pyramid_roi_align([rois] + x, self.pool_size, self.image_shape)   # 3000 (3x1000), 256, 7, 7
        x = self.conv1(self.padding(x))
        x = self.bn1(x)
        x = self.relu(x)
        x = self.conv2(self.padding(x))
        x = self.bn2(x)
        x = self.relu(x)
        x = self.conv3(self.padding(x))
        x = self.bn3(x)
        x = self.relu(x)
        x = self.conv4(self.padding(x))
        x = self.bn4(x)
        x = self.relu(x)
        x = self.deconv(x)
        x = self.relu(x)
        x = self.conv5(x)
        x = self.sigmoid(x)

        return x
