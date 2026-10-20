# -*- coding: utf-8 -*-
from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

doc = Document()

# ── Page setup ──
for section in doc.sections:
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

# ── Helper: set paragraph font ──
def set_font(run, name_cn='宋体', name_en='Times New Roman', size=12, bold=False):
    run.font.size = Pt(size)
    run.font.name = name_en
    run.font.bold = bold
    run.element.rPr.rFonts.set(qn('w:eastAsia'), name_cn)

def add_heading_text(text, level=1, center=True):
    if level == 0:
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        set_font(run, '黑体', 'Times New Roman', 16, bold=True)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.space_before = Pt(12)
        return p
    elif level == 1:
        p = doc.add_paragraph()
        run = p.add_run(text)
        set_font(run, '黑体', 'Times New Roman', 14, bold=True)
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(6)
        return p
    elif level == 2:
        p = doc.add_paragraph()
        run = p.add_run(text)
        set_font(run, '黑体', 'Times New Roman', 12, bold=True)
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(4)
        return p
    elif level == 3:
        p = doc.add_paragraph()
        run = p.add_run(text)
        set_font(run, '黑体', 'Times New Roman', 12, bold=True)
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(2)
        return p

def add_body(text, first_line_indent=True, space_after=6):
    p = doc.add_paragraph()
    run = p.add_run(text)
    set_font(run, '宋体', 'Times New Roman', 12)
    pf = p.paragraph_format
    pf.line_spacing = Pt(22)
    pf.space_after = Pt(space_after)
    if first_line_indent:
        pf.first_line_indent = Cm(0.74)
    return p

def add_body_bold_prefix(prefix, text, first_line_indent=True):
    p = doc.add_paragraph()
    r1 = p.add_run(prefix)
    set_font(r1, '宋体', 'Times New Roman', 12, bold=True)
    r2 = p.add_run(text)
    set_font(r2, '宋体', 'Times New Roman', 12)
    pf = p.paragraph_format
    pf.line_spacing = Pt(22)
    pf.space_after = Pt(4)
    if first_line_indent:
        pf.first_line_indent = Cm(0.74)
    return p

def add_table(headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # header
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ''
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(h)
        set_font(run, '宋体', 'Times New Roman', 11, bold=True)
    # data
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            cell = table.rows[r_idx + 1].cells[c_idx]
            cell.text = ''
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(str(val))
            set_font(run, '宋体', 'Times New Roman', 11)
    return table


# ════════════════════════════════════════════════════
# Title
# ════════════════════════════════════════════════════
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('基于物联网的室内温湿度智能采集与监控系统可行性研究')
set_font(run, '黑体', 'Times New Roman', 18, bold=True)
p.paragraph_format.space_after = Pt(16)

# ── Abstract ──
add_heading_text('摘　要', level=2, center=True)
add_body(
    '针对当前中小型场所温湿度监测依赖人工记录、数据不连续、异常响应滞后等实际问题，'
    '本文提出一种基于物联网技术的室内温湿度智能采集与监控系统方案。系统以STM32F103C8T6微控制器为核心，'
    '搭载DHT22数字温湿度传感器实现环境数据采集，通过0.96寸OLED屏幕进行本地实时显示，'
    '并借助ESP8266 WiFi模块以MQTT协议将数据上传至云平台，支持远程监控与历史数据查询。'
    '本文从需求分析、市场现状、技术可行性、经济可行性、操作可行性、风险评估等多维度对该系统方案进行了系统论证，'
    '结果表明：所选技术方案成熟可靠，单节点硬件成本控制在100元以内，开发与运维难度适中，'
    '具备良好的实用价值和推广前景。本文最后给出了项目实施进度计划、风险应对措施及后续优化方向，'
    '为同类物联网环境监测项目的立项与实施提供了参考依据。'
)

add_heading_text('关键词', level=2, center=True)
add_body('物联网；温湿度监测；STM32；MQTT；可行性分析')

# ════════════════════════════════════════════════════
# 1 引言
# ════════════════════════════════════════════════════
add_heading_text('1　引言', level=1)
add_body(
    '环境温湿度是影响农业生产、仓储物流、实验室管理、设备运维等领域的重要参数。'
    '据调研，当前大量中小型场所仍采用传统温湿度计进行人工定时读数和纸质记录，'
    '这种方式存在数据采样密度低、记录易出错、无法实时预警等固有缺陷[1]。'
    '一旦环境参数超出安全范围且未能及时发现，往往造成农作物减产、药品变质、电子设备受潮等直接经济损失。'
)
add_body(
    '近年来，物联网（Internet of Things, IoT）技术发展迅速，低功耗微控制器、数字传感器和无线通信模块的成本持续下降，'
    '为环境监测领域提供了低成本、高可靠的技术路径[2]。国家"十四五"规划明确提出加快数字化转型和智慧物联基础设施建设，'
    '各地在智慧农业、数字校园、智能仓储等方向持续加大投入，为物联网环境监测类项目提供了良好的政策环境。'
)
add_body(
    '然而，现有商用物联网监测方案多采用封闭生态设计，存在二次开发受限、大规模部署成本递增、数据存储依赖厂商等问题[3]。'
    '对于预算有限且有定制化需求的教学科研和小型应用场景，亟需一种成本可控、技术开放、易于部署和维护的替代方案。'
)
add_body(
    '基于上述背景，本文提出一种基于STM32单片机和DHT22传感器的温湿度智能采集与监控系统方案，'
    '并从需求分析、技术路线、经济成本、操作实施、政策合规、风险评估等多个维度进行系统可行性论证，'
    '旨在为项目的立项决策和后续实施提供科学依据。'
)

# ════════════════════════════════════════════════════
# 2 系统需求分析
# ════════════════════════════════════════════════════
add_heading_text('2　系统需求分析', level=1)
add_heading_text('2.1　功能需求', level=2)
add_body(
    '通过对目标应用场景（农业温室、仓储、教室、实验室等）的调研分析，系统应满足以下核心功能需求：'
)
add_body_bold_prefix('（1）数据采集：', '通过DHT22传感器实时采集环境温度与湿度数据，温度测量范围-40℃~80℃，湿度测量范围0~100%RH，采集周期可配置（默认5秒/次）。')
add_body_bold_prefix('（2）本地显示：', '在0.96寸OLED屏幕上实时显示当前温度和湿度数值，刷新周期与采集周期同步。')
add_body_bold_prefix('（3）阈值报警：', '用户可通过物理按键设定温湿度上下限阈值，当实时数据超出阈值时，系统驱动蜂鸣器鸣响和LED闪烁进行声光报警，持续至数据恢复正常。')
add_body_bold_prefix('（4）无线传输：', '通过ESP8266 WiFi模块接入局域网，采用MQTT协议将温湿度数据定时推送至云平台（OneNet、EMQX等）。')
add_body_bold_prefix('（5）远程监控：', '提供Web端数据展示界面，支持实时数值显示、历史数据曲线绘制、时间段筛选及远程阈值配置。')

add_heading_text('2.2　性能需求', level=2)
add_body('系统应满足的主要性能指标如表1所示。', space_after=4)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('表1　系统主要性能指标')
set_font(run, '宋体', 'Times New Roman', 11, bold=True)
p.paragraph_format.space_before = Pt(8)
p.paragraph_format.space_after = Pt(4)
add_table(
    ['性能指标', '目标值'],
    [
        ['温度采集精度', '±0.5℃'],
        ['湿度采集精度', '±2%RH'],
        ['采集周期', '可配置，默认5秒/次'],
        ['报警响应延迟', '≤2秒'],
        ['数据上传成功率', '≥98%'],
        ['连续无故障运行时长', '≥72小时'],
        ['WiFi断线自动重连时间', '≤30秒'],
    ]
)

add_heading_text('2.3　使用与环境需求', level=2)
add_body(
    '在使用层面，系统应满足即插即用、操作简便的基本要求。硬件采用USB或5V适配器供电，'
    '传感器通过杜邦线连接，支持桌面放置或壁挂安装，无需专业施工。系统适用工作温度为-20℃~60℃，'
    '适用湿度为5%~95%RH（无凝露），供电电压5V DC，功耗低于500mA，符合低压电气安全标准。'
    '通信建议配合TLS加密以保障数据传输安全。'
)

# ════════════════════════════════════════════════════
# 3 市场与应用分析
# ════════════════════════════════════════════════════
add_heading_text('3　市场与应用分析', level=1)
add_heading_text('3.1　应用场景', level=2)
add_body(
    '本系统面向多种室内环境监测场景，主要包括：农业温室大棚的温湿度监控与通风灌溉决策支持；'
    '仓储物流中心的环境参数监测与异常预警；学校教室和实验室的环境数据采集与教学演示；'
    '智能家居中的环境感知节点；机房及设备间的运行环境监控。'
)
add_heading_text('3.2　行业现状与竞品分析', level=2)
add_body(
    '目前市场上的温湿度监测方案可归纳为三类。第一类为传统温湿度计，成本低廉但不具备数据自动记录和远程查看能力，'
    '依赖人工操作。第二类为工业级环境监测系统，功能完善、精度高，但单套设备价格通常在数千至数万元，'
    '部署复杂，维护成本高，不适合小型场所。第三类为商用物联网方案（如小米、涂鸦智能等），'
    '设备集成度较好，但多采用封闭生态设计，二次开发能力有限，且按设备数量收取平台服务费，规模化部署成本显著上升。'
)
add_heading_text('3.3　本方案优势', level=2)
add_body(
    '与上述方案相比，本系统具有以下竞争优势：硬件成本极低，单节点不超过100元；'
    '技术架构完全开源，支持自由扩展和二次开发；数据存储在自建服务器，用户拥有完全的数据自主权；'
    '部署简便，后期维护依赖通用器件，无厂商锁定风险。表2给出了本方案与主流方案的对比。'
)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('表2　本方案与主流方案对比')
set_font(run, '宋体', 'Times New Roman', 11, bold=True)
p.paragraph_format.space_before = Pt(8)
p.paragraph_format.space_after = Pt(4)
add_table(
    ['对比维度', '本项目方案', '工业级方案', '商用物联网方案'],
    [
        ['单节点成本', '≈80~100元', '2000~10000元', '150~500元'],
        ['二次开发能力', '完全开源', '封闭，受限', '部分开放'],
        ['部署难度', '低', '高', '中等'],
        ['数据自主性', '完全自主', '取决于厂商', '依赖厂商云平台'],
        ['维护成本', '低', '高', '中等'],
    ]
)

# ════════════════════════════════════════════════════
# 4 可行性分析
# ════════════════════════════════════════════════════
add_heading_text('4　可行性分析', level=1)
add_heading_text('4.1　技术可行性', level=2)
add_body(
    '系统核心技术组件均选用市场上成熟且广泛使用的产品。主控芯片STM32F103C8T6是基于ARM Cortex-M3架构的32位微控制器，'
    '主频72MHz，片上资源丰富，配合STM32CubeMX和HAL库可显著降低底层驱动开发难度[4]。'
    'DHT22为数字输出型温湿度传感器，采用单总线通信协议，接口简单、驱动库成熟[5]。'
    'ESP8266是物联网领域应用最广泛的WiFi模组之一，支持AT指令集和SDK开发方式，MQTT协议库资源充足[6][7]。'
    '0.96寸OLED显示屏采用I2C接口，开源驱动库在GitHub和各技术社区均有大量可参考实现。'
    '云平台方面，OneNet和阿里云IoT平台均提供免费接入额度和完善的开发文档，也可自行搭建EMQX服务器实现完全自主可控。'
)
add_body(
    '综上，本系统所涉及的硬件器件、开发工具、通信协议和云平台方案均经过大量工程实践验证，技术风险低，功能实现有充分保障。'
)
add_heading_text('4.2　经济可行性', level=2)
add_body('本系统单节点硬件物料清单及成本估算如表3所示。', space_after=4)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('表3　单节点硬件成本估算')
set_font(run, '宋体', 'Times New Roman', 11, bold=True)
p.paragraph_format.space_before = Pt(8)
p.paragraph_format.space_after = Pt(4)
add_table(
    ['器件名称', '数量', '单价（元）', '小计（元）'],
    [
        ['STM32F103C8T6最小系统板', '1', '15', '15'],
        ['DHT22温湿度传感器模块', '1', '12', '12'],
        ['0.96寸OLED显示屏', '1', '10', '10'],
        ['ESP8266（ESP-01S）模块', '1', '8', '8'],
        ['蜂鸣器、LED、按键、电阻', '若干', '5', '5'],
        ['面包板、杜邦线、USB线', '1套', '10', '10'],
        ['5V电源适配器', '1', '8', '8'],
        ['合计', '', '', '≈68'],
    ]
)
add_body(
    '开发工具方面，Keil MDK（社区版）、STM32CubeMX、VS Code等均为免费软件；云平台OneNet提供免费设备接入额度；'
    'Web前端采用开源框架开发，无额外授权费用。综合计算，单节点全成本（含硬件、软件、平台）不超过100元，'
    '批量采购可压缩至60元左右，具有显著的经济优势。'
)
add_heading_text('4.3　操作与实施可行性', level=2)
add_body(
    '硬件层面，系统搭建在面包板上完成，无需焊接，普通学生依据电路图即可独立完成。'
    '软件层面，Keil MDK和STM32CubeMX提供完善的工程管理和代码生成功能，HAL库封装了底层寄存器操作，降低了编码难度。'
    '设备部署仅需接通电源并配置WiFi热点信息，无需专业安装施工。'
    'Web端采用常见的HTML、JavaScript配合后端框架实现，维护和二次修改的门槛较低。'
    '后期可将面包板原型转为PCB印制电路板，进一步提升产品的可靠性和美观度。'
)
add_heading_text('4.4　政策与环境可行性', level=2)
add_body(
    '系统所使用的电子元器件均符合国家电子产品质量标准，不涉及无线电管制设备。'
    'ESP8266工作在2.4GHz ISM（工业、科学和医疗）免许可频段，合法使用无政策障碍。'
    '项目面向教学、科研和一般性环境监测应用，不涉及国家安全、隐私保护等敏感领域。'
    '此外，国家"十四五"规划及各地方政策持续推动智慧农业、数字校园、智能仓储建设，本项目方向与国家政策导向高度吻合[8]。'
)
add_heading_text('4.5　风险评估', level=2)
add_body('项目实施过程中可能面临的主要风险及评估如表4所示。', space_after=4)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('表4　项目主要风险评估')
set_font(run, '宋体', 'Times New Roman', 11, bold=True)
p.paragraph_format.space_before = Pt(8)
p.paragraph_format.space_after = Pt(4)
add_table(
    ['风险项', '等级', '描述'],
    [
        ['传感器长期数据漂移', '低', 'DHT22在长期使用中可能出现轻微精度下降，可通过定期校准解决'],
        ['WiFi信号不稳定', '中', '室内墙体遮挡可能导致信号衰减，需合理选择模块位置或外接天线'],
        ['供电中断', '低', '可增加备用电池模块或小型UPS保障供电连续性'],
        ['开发进度延期', '中', '嵌入式系统调试周期不确定性较大，需在进度计划中预留缓冲'],
        ['数据安全风险', '低', '小规模系统风险可控，正式部署时启用TLS加密传输'],
    ]
)

# ════════════════════════════════════════════════════
# 5 实施方案与进度规划
# ════════════════════════════════════════════════════
add_heading_text('5　实施方案与进度规划', level=1)
add_body('项目整体划分为六个阶段，计划总工期约12周，具体安排如表5所示。', space_after=4)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('表5　项目实施进度计划')
set_font(run, '宋体', 'Times New Roman', 11, bold=True)
p.paragraph_format.space_before = Pt(8)
p.paragraph_format.space_after = Pt(4)
add_table(
    ['阶段', '时间', '主要工作内容'],
    [
        ['方案设计', '第1~2周', '需求细化、系统架构设计、器件选型确认、电路原理图绘制'],
        ['硬件搭建', '第3~4周', '器件采购、电路搭建、传感器基础通信调试'],
        ['固件开发', '第5~7周', '传感器驱动、OLED显示、按键交互、报警逻辑、WiFi通信、MQTT数据上传'],
        ['Web端开发', '第8~9周', '后端数据接收服务、前端实时数据展示、历史曲线、阈值配置'],
        ['联调测试', '第10~11周', '软硬件联合调试、功能测试、性能测试、72小时稳定性测试'],
        ['部署验收', '第12周', '现场部署、技术文档整理、验收测试与报告编写'],
    ]
)

# ════════════════════════════════════════════════════
# 6 风险应对措施
# ════════════════════════════════════════════════════
add_heading_text('6　风险应对措施', level=1)
add_body('针对第4.5节识别的主要风险，制定如下具体应对措施：')
add_body_bold_prefix('（1）硬件连接故障：', '杜邦线连接在振动环境下可能松动，导致传感器通信异常。应对方案为关键连接点采用排针焊接加固，定期巡检接线状态。')
add_body_bold_prefix('（2）WiFi连接不稳定：', '实现固件级自动重连机制，断线后30秒内尝试重新连接；在设备外壳上增加WiFi状态指示灯，便于运维人员快速判断网络状态；必要时为ESP8266外接高增益天线。')
add_body_bold_prefix('（3）传感器凝露损坏：', '高湿环境中传感器表面可能凝结水珠，影响测量精度甚至损坏器件。应对方案为加装防水透气外壳，避免传感器探头直接暴露于水汽环境中。')
add_body_bold_prefix('（4）网络中断期间数据丢失：', '在MCU端设计环形缓冲区，本地缓存最近24小时的采集数据；网络恢复后自动补传缺失数据，确保数据完整性。')
add_body_bold_prefix('（5）供电不稳定：', '选用额定输出电流不低于1A的5V电源适配器，在电源输入端增加100μF电解电容和0.1μF陶瓷电容进行滤波，降低纹波干扰。')

# ════════════════════════════════════════════════════
# 7 效益分析
# ════════════════════════════════════════════════════
add_heading_text('7　效益分析', level=1)
add_heading_text('7.1　实用效益', level=2)
add_body(
    '系统投入使用后，可实现7×24小时不间断温湿度监测，数据采集密度较人工记录方式提升两个数量级以上，'
    '数据完整性和准确性显著提高。超限报警功能将环境异常的发现时间从"小时级"缩短至"秒级"，'
    '有效降低因环境失控造成的经济损失。远程监控功能减少了人员到场巡检频次，降低了运维人力成本。'
    '历史数据的长期积累为环境分析和质量追溯提供了可靠的数据基础。'
)
add_heading_text('7.2　推广效益', level=2)
add_body(
    '系统采用模块化架构设计，核心硬件平台（STM32+WiFi）具有良好的通用性。'
    '通过更换传感器型号即可适配光照强度、CO₂浓度、土壤含水量等其他环境参数的监测需求，具备较强的场景扩展能力。'
    '单节点极低的成本使得大规模密集部署成为经济可行的选择。系统技术方案完全开源，'
    '可为同类项目的开发提供直接参考，具有良好的教学示范和技术推广价值。'
)

# ════════════════════════════════════════════════════
# 8 结论与展望
# ════════════════════════════════════════════════════
add_heading_text('8　结论与展望', level=1)
add_body(
    '本文围绕基于物联网的室内温湿度智能采集与监控系统，从需求分析、市场现状、技术路线、'
    '经济成本、操作实施、政策合规和风险评估七个维度进行了系统可行性论证。研究表明，'
    '本系统所采用的STM32+DHT22+ESP8266技术方案成熟可靠、资料充足；单节点硬件成本不超过100元，'
    '经济性突出；系统部署和维护难度低，适合非专业用户操作；项目方向符合国家智慧物联政策导向，'
    '风险总体可控。综合评估认为，本项目在技术上可实现、经济上可承受、操作上可落地，建议立项实施。'
)
add_body(
    '在后续工作中，可从以下方向进行拓展优化：引入低功耗微控制器（如STM32L系列）和NB-IoT通信技术，'
    '将系统适配至无WiFi覆盖的户外场景；构建多传感器节点无线传感网络，实现分布式环境监测与数据融合分析；'
    '在Web端引入智能告警推送机制（微信/短信通知）和机器学习趋势预测功能，进一步提升系统的智能化水平。'
)

# ════════════════════════════════════════════════════
# 参考文献
# ════════════════════════════════════════════════════
add_heading_text('参考文献', level=1)
refs = [
    '[1] 王志良, 闫丰亭. 物联网工程概论[M]. 北京: 机械工业出版社, 2020.',
    '[2] 刘云浩. 物联网导论[M]. 第4版. 北京: 科学出版社, 2022.',
    '[3] 张毅, 李伟. 基于STM32和MQTT的远程环境监测系统设计[J]. 电子技术应用, 2023, 49(5): 78-82.',
    '[4] STMicroelectronics. STM32F103x8/STM32F103xB Datasheet[Z]. 2023.',
    '[5] Aosong Electronics. DHT22 Product Manual[Z]. 2021.',
    '[6] Espressif Systems. ESP8266 AT Instruction Set[Z]. 2022.',
    '[7] OASIS. MQTT Version 5.0 Specification[S]. 2019.',
    '[8] 国务院. "十四五"数字经济发展规划[Z]. 2022.',
]
for ref in refs:
    add_body(ref, first_line_indent=False, space_after=2)

# ════════════════════════════════════════════════════
# 实验总结与心得体会
# ════════════════════════════════════════════════════
add_heading_text('实验总结与心得体会', level=1)
add_body(
    '通过本次实验，我系统学习了学术小论文的规范撰写方法和工程项目可行性论证的完整流程。'
    '从文献调研、需求分析到多维度可行性论证，逐步培养了将工程问题转化为结构化论述的能力。'
    '撰写过程中我深刻体会到，可行性论证必须建立在具体数据和实际调研基础上，空泛的定性描述缺乏说服力。'
    '通过器件选型和成本估算，我对物联网项目的全貌有了更清晰的认知。'
    '不足之处在于竞品调研深度有限、参考文献引用数量偏少，后续应加强文献检索和行业分析能力，提升论文的学术规范性和论证深度。'
)

# ── Save ──
output_path = r'E:\pycharm\project\pythonProject1\SuperStar - 副本\温湿度采集系统可行性研究.docx'
doc.save(output_path)
print(f'Word document saved to: {output_path}')
