import re

# 1. 实体店招/加盟商死刑词（只要网点名称或地址含此类词汇，100% 为假住宅）
STOREFRONT_KILL_PATTERNS = re.compile(
    r"\b("
    r"Storage|Self\s*Storage|Mini\s*Storage|"          # 仓储类（假住宅重灾区）
    r"Pack|Ship|Postal|Mail|Parcel|Express|Drop|"      # 打包快递店
    r"Print|Sign|Graphic|Copy|"                        # 图文快印
    r"Auto|Motors|Tire|Repair|Hardware|Lumber|"        # 汽修五金
    r"Pharmacy|Market|Liquor|Grocers|Cleaners|"        # 杂货干洗
    r"Depot|Square|Plaza|Center|Centre|Mall|Galleria|" # 广场购物中心
    r"Suites|Executive|Coworking|Offices|Workspace"    # 共享办公
    r")\b",
    re.IGNORECASE
)

# 2. 纯正单栋住宅（SFH）特征加权
RESIDENTIAL_STREET_SUFFIX = re.compile(
    r"\b(Lane|Ln|Court|Ct|Circle|Cir|Terrace|Ter|Way|Trail|Trl|Drive|Dr|Place|Pl)\b",
    re.IGNORECASE
)

COMMERCIAL_ARTERY_SUFFIX = re.compile(
    r"\b(Blvd|Boulevard|Pkwy|Parkway|Hwy|Highway|Expressway|Turnpike|Route|Rt)\b",
    re.IGNORECASE
)

def evaluate_address_potential(item):
    street = item.get("street", "")
    store_name = item.get("store_name", "") or item.get("location_name", "")
    full_check_text = f"{street} {store_name}"
    
    # 规则 1：网点名称或街道命中商业招牌词汇，直接物理拒绝
    if STOREFRONT_KILL_PATTERNS.search(full_check_text):
        return 999, "Storefront / Commercial Retailer"

    # 规则 2：强制排除 Suite / PMB / Box 等非住宅二级特征
    if re.search(r"\b(Suite|Ste|PMB|Box|Rm|Room|Dept)\b", street, re.IGNORECASE):
        return 999, "Commercial secondary unit (Suite/PMB/Box)"

    score = 50

    # 规则 3：住宅道路形态加权（Lane, Ct, Cir 极大几率位于低密度纯居住区）
    if RESIDENTIAL_STREET_SUFFIX.search(street):
        score -= 30
    elif COMMERCIAL_ARTERY_SUFFIX.search(street):
        score += 30

    # 规则 4：纯门牌无二级扩展号（极品独栋形态）优先排查
    if not re.search(r"\b(#|unit|apt)\b", street, re.IGNORECASE):
        score -= 40
    elif re.search(r"\bapt\s*([1-9]|[1-9]\d{0,1}[a-z]?)\b", street, re.IGNORECASE):
        score -= 20  # 低楼层短字母常规公寓单元

    return score, "Candidate"
