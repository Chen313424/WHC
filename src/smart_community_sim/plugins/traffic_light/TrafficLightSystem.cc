/*
 * 智慧社区红绿灯时序控制系统插件（统一时序：红灯10s / 绿灯15s / 黄灯5s）。
 *
 * 用法（写在世界 SDF 的 <world><plugin> 里）：
 *   <plugin filename="libTrafficLightSystem.so"
 *           name="gz::sim::systems::TrafficLight">
 *     <prefix>traffic_light</prefix>   # 红绿灯模型名前缀
 *     <green_time>15</green_time>      # 绿灯时长（秒）
 *     <yellow_time>5</yellow_time>     # 黄灯时长（秒）
 *     <red_time>10</red_time>          # 红灯时长（秒）
 *   </plugin>
 *
 * 约定：每个红绿灯模型内部，三盏灯泡是名为 red / yellow / green 的
 * <visual>（名称固定，与灯泡在模型内的实际排列方向无关）。
 * 世界插件 Configure 阶段模型实体尚未创建，因此灯泡实体在首次 PreUpdate
 * 时惰性发现，之后每个 PreUpdate 按当前仿真时间统一切换所有灯：
 *   绿(15s) -> 黄(5s) -> 红(10s) -> 循环
 * 所有红绿灯同一时序（同步），仅按灯泡名称 red/yellow/green 上色。
 */
#include <chrono>
#include <string>
#include <vector>

#include <gz/math/Color.hh>
#include <gz/plugin/Register.hh>
#include <gz/sim/components/Material.hh>
#include <gz/sim/components/Model.hh>
#include <gz/sim/components/Name.hh>
#include <gz/sim/components/ParentEntity.hh>
#include <gz/sim/components/Visual.hh>
#include <gz/sim/EntityComponentManager.hh>
#include <gz/sim/System.hh>
#include <sdf/Material.hh>

namespace gz
{
namespace sim
{
namespace systems
{

enum class Lamp { RED, YELLOW, GREEN };

class TrafficLight
    : public System,
      public ISystemConfigure,
      public ISystemPreUpdate
{
  public: void Configure(const Entity &_entity,
                         const std::shared_ptr<const sdf::Element> &_sdf,
                         EntityComponentManager &_ecm,
                         EventManager &_eventMgr) override;

  public: void PreUpdate(const UpdateInfo &_info,
                         EntityComponentManager &_ecm) override;

  private: void DiscoverBulbs(EntityComponentManager &_ecm);

  private: std::vector<Entity> red_;
  private: std::vector<Entity> yellow_;
  private: std::vector<Entity> green_;
  private: bool discovered_{false};
  private: double greenTime_{15.0};
  private: double yellowTime_{5.0};
  private: double redTime_{10.0};
  private: std::string prefix_{"traffic_light"};
};

namespace
{
// 向上找所属 model 实体，找不到返回 kNullEntity。
Entity FindModelAncestor(const Entity &_e, EntityComponentManager &_ecm)
{
  Entity cur = _e;
  while (auto parent = _ecm.Component<components::ParentEntity>(cur))
  {
    cur = parent->Data();
    if (_ecm.Component<components::Model>(cur))
      return cur;
  }
  return kNullEntity;
}

bool StartsWith(const std::string &_s, const std::string &_prefix)
{
  return _s.compare(0, _prefix.size(), _prefix) == 0;
}
}  // namespace

void TrafficLight::Configure(const Entity &_entity,
                             const std::shared_ptr<const sdf::Element> &_sdf,
                             EntityComponentManager &_ecm,
                             EventManager &_eventMgr)
{
  (void)_entity;
  (void)_ecm;
  (void)_eventMgr;

  if (_sdf->HasElement("green_time"))
    greenTime_ = _sdf->Get<double>("green_time");
  if (_sdf->HasElement("yellow_time"))
    yellowTime_ = _sdf->Get<double>("yellow_time");
  if (_sdf->HasElement("red_time"))
    redTime_ = _sdf->Get<double>("red_time");
  if (_sdf->HasElement("prefix"))
    prefix_ = _sdf->Get<std::string>("prefix");
}

void TrafficLight::DiscoverBulbs(EntityComponentManager &_ecm)
{
  red_.clear();
  yellow_.clear();
  green_.clear();

  _ecm.Each<components::Name, components::Visual>(
      [&](const Entity &_e, const components::Name *_name,
          const components::Visual *) -> bool
      {
        const std::string name = _name->Data();
        Lamp lamp;
        if (name == "red")
          lamp = Lamp::RED;
        else if (name == "yellow")
          lamp = Lamp::YELLOW;
        else if (name == "green")
          lamp = Lamp::GREEN;
        else
          return true;

        Entity model = FindModelAncestor(_e, _ecm);
        if (model == kNullEntity)
          return true;
        const std::string modelName =
            _ecm.Component<components::Name>(model)->Data();
        if (!StartsWith(modelName, prefix_))
          return true;

        if (lamp == Lamp::RED)
          red_.push_back(_e);
        else if (lamp == Lamp::YELLOW)
          yellow_.push_back(_e);
        else
          green_.push_back(_e);
        return true;
      });

  gzdbg << "TrafficLight: 找到 " << (red_.size() + yellow_.size() + green_.size())
        << " 盏灯 (red=" << red_.size() << ", yellow=" << yellow_.size()
        << ", green=" << green_.size() << ", prefix='" << prefix_ << "')\n";
}

void TrafficLight::PreUpdate(const UpdateInfo &_info,
                             EntityComponentManager &_ecm)
{
  if (!discovered_)
  {
    DiscoverBulbs(_ecm);
    discovered_ = true;
  }

  if (red_.empty() && yellow_.empty() && green_.empty())
    return;

  const double t = std::chrono::duration<double>(_info.simTime).count();
  const double cycle = greenTime_ + yellowTime_ + redTime_;
  double phase = std::fmod(t, cycle);
  if (phase < 0)
    phase += cycle;

  // 相位：0~green=绿; green~green+yellow=黄; 其余=红。
  Lamp active;
  if (phase < greenTime_)
    active = Lamp::GREEN;
  else if (phase < greenTime_ + yellowTime_)
    active = Lamp::YELLOW;
  else
    active = Lamp::RED;

  const gz::math::Color off(0.05f, 0.05f, 0.05f, 1.0f);
  const gz::math::Color offDiffuse(0.12f, 0.12f, 0.12f, 1.0f);
  const gz::math::Color red(1.0f, 0.0f, 0.0f, 1.0f);
  const gz::math::Color yellow(1.0f, 0.75f, 0.0f, 1.0f);
  const gz::math::Color green(0.0f, 0.7f, 0.2f, 1.0f);

  auto apply = [&](const std::vector<Entity> &_bulbs,
                   const gz::math::Color &_on, bool _active)
  {
    for (const Entity &e : _bulbs)
    {
      sdf::Material mat;
      mat.SetEmissive(_active ? _on : off);
      mat.SetDiffuse(_active ? _on : offDiffuse);
      _ecm.SetComponentData<components::Material>(e, mat);
    }
  };

  apply(red_, red, active == Lamp::RED);
  apply(yellow_, yellow, active == Lamp::YELLOW);
  apply(green_, green, active == Lamp::GREEN);
}

}  // namespace systems
}  // namespace sim
}  // namespace gz

GZ_ADD_PLUGIN(
    gz::sim::systems::TrafficLight,
    gz::sim::System,
    gz::sim::systems::TrafficLight::ISystemConfigure,
    gz::sim::systems::TrafficLight::ISystemPreUpdate)

// 同时注册无版本命名空间的别名，保证世界 SDF 里写的
// name="gz::sim::systems::TrafficLight" 能命中（与内置插件命名风格一致）。
GZ_ADD_PLUGIN_ALIAS(
    gz::sim::systems::TrafficLight,
    "gz::sim::systems::TrafficLight")
