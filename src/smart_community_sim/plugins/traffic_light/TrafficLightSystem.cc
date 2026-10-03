/*
 * 智慧社区红绿灯时序控制系统插件。
 *
 * 用法（写在世界 SDF 的 <world><plugin> 里）：
 *   <plugin filename="libTrafficLightSystem.so"
 *           name="gz::sim::systems::TrafficLight">
 *     <ns_prefix>traffic_light_ns</ns_prefix>   # 南北向红绿灯模型名前缀
 *     <ew_prefix>traffic_light_ew</ew_prefix>   # 东西向红绿灯模型名前缀
 *     <green_time>15</green_time>               # 绿灯时长（秒）
 *     <yellow_time>3</yellow_time>              # 黄灯时长（秒）
 *   </plugin>
 *
 * 约定：每个红绿灯模型内部，三盏灯泡是名为 red / yellow / green 的
 * <visual>。世界插件 Configure 阶段模型实体尚未创建，因此灯泡实体在
 * 首次 PreUpdate 时惰性发现，之后每个 PreUpdate 按当前仿真时间切换两组
 * 灯的 emissive/diffuse 颜色：
 *   NS 绿 -> NS 黄 -> EW 绿 -> EW 黄 -> 循环（红灯时长 = 对向绿+黄）。
 */
#include <chrono>
#include <cmath>
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

enum class Group { NS, EW };
enum class Lamp { RED, YELLOW, GREEN };
enum class State { RED, YELLOW, GREEN };

struct Bulb
{
  Entity entity;
  Group group;
  Lamp lamp;
};

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

  private: std::vector<Bulb> bulbs_;
  private: bool discovered_{false};
  private: double greenTime_{15.0};
  private: double yellowTime_{3.0};
  private: std::string nsPrefix_{"traffic_light_ns"};
  private: std::string ewPrefix_{"traffic_light_ew"};
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
  if (_sdf->HasElement("ns_prefix"))
    nsPrefix_ = _sdf->Get<std::string>("ns_prefix");
  if (_sdf->HasElement("ew_prefix"))
    ewPrefix_ = _sdf->Get<std::string>("ew_prefix");
}

void TrafficLight::DiscoverBulbs(EntityComponentManager &_ecm)
{
  bulbs_.clear();
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

        Group group;
        if (StartsWith(modelName, nsPrefix_))
          group = Group::NS;
        else if (StartsWith(modelName, ewPrefix_))
          group = Group::EW;
        else
          return true;

        bulbs_.push_back({_e, group, lamp});
        return true;
      });

  gzdbg << "TrafficLight: 找到 " << bulbs_.size()
        << " 盏灯 (NS prefix='" << nsPrefix_
        << "', EW prefix='" << ewPrefix_ << "')\n";
}

void TrafficLight::PreUpdate(const UpdateInfo &_info,
                             EntityComponentManager &_ecm)
{
  if (!discovered_)
  {
    DiscoverBulbs(_ecm);
    discovered_ = true;
  }

  if (bulbs_.empty())
    return;

  const double t = std::chrono::duration<double>(_info.simTime).count();
  const double cycle = 2.0 * (greenTime_ + yellowTime_);
  double phase = std::fmod(t, cycle);
  if (phase < 0)
    phase += cycle;

  // 相位：0~green=NS绿; green~green+yellow=NS黄;
  //       green+yellow~2green+yellow=EW绿; 最后=EW黄。
  State nsState;
  State ewState;
  if (phase < greenTime_)
  {
    nsState = State::GREEN;
    ewState = State::RED;
  }
  else if (phase < greenTime_ + yellowTime_)
  {
    nsState = State::YELLOW;
    ewState = State::RED;
  }
  else if (phase < 2 * greenTime_ + yellowTime_)
  {
    nsState = State::RED;
    ewState = State::GREEN;
  }
  else
  {
    nsState = State::RED;
    ewState = State::YELLOW;
  }

  const gz::math::Color off(0.05f, 0.05f, 0.05f, 1.0f);
  const gz::math::Color offDiffuse(0.12f, 0.12f, 0.12f, 1.0f);
  const gz::math::Color red(1.0f, 0.0f, 0.0f, 1.0f);
  const gz::math::Color yellow(1.0f, 0.75f, 0.0f, 1.0f);
  const gz::math::Color green(0.0f, 0.7f, 0.2f, 1.0f);

  for (const auto &b : bulbs_)
  {
    const State s = (b.group == Group::NS) ? nsState : ewState;
    bool on = false;
    gz::math::Color color = off;
    if (s == State::GREEN && b.lamp == Lamp::GREEN)
    {
      on = true;
      color = green;
    }
    else if (s == State::YELLOW && b.lamp == Lamp::YELLOW)
    {
      on = true;
      color = yellow;
    }
    else if (s == State::RED && b.lamp == Lamp::RED)
    {
      on = true;
      color = red;
    }

    sdf::Material mat;
    mat.SetEmissive(color);
    mat.SetDiffuse(on ? color : offDiffuse);
    _ecm.SetComponentData<components::Material>(b.entity, mat);
  }
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
