//
// Copyright 2016 Pixar
//
// Licensed under the Apache License, Version 2.0 (the "Apache License")
// with the following modification; you may not use this file except in
// compliance with the Apache License and the following modification to it:
// Section 6. Trademarks. is deleted and replaced with:
//
// 6. Trademarks. This License does not grant permission to use the trade
//    names, trademarks, service marks, or product names of the Licensor
//    and its affiliates, except as required to comply with Section 4(c) of
//    the License and to reproduce the content of the NOTICE file.
//
// You may obtain a copy of the Apache License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the Apache License with the above modification is
// distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied. See the Apache License for the specific
// language governing permissions and limitations under the Apache License.
//
#include "pxr/imaging/hd/engine.h"

#include "pxr/imaging/hd/debugCodes.h"
#include "pxr/imaging/hd/drawItem.h"
#include "pxr/imaging/hd/perfLog.h"
#include "pxr/imaging/hd/renderContextCaps.h"
#include "pxr/imaging/hd/renderDelegate.h"
#include "pxr/imaging/hd/renderDelegateRegistry.h"
#include "pxr/imaging/hd/renderIndex.h"
#include "pxr/imaging/hd/renderIndexManager.h"
#include "pxr/imaging/hd/renderPass.h"
#include "pxr/imaging/hd/renderPassState.h"
#include "pxr/imaging/hd/resourceRegistry.h"
#include "pxr/imaging/hd/rprim.h"
#include "pxr/imaging/hd/shader.h"
#include "pxr/imaging/hd/task.h"
#include "pxr/imaging/hd/tokens.h"

#include <sstream>

PXR_NAMESPACE_OPEN_SCOPE


HdEngine::HdEngine() 
 : _taskContext()
{
}

HdEngine::~HdEngine()
{
}

// static
HdRenderIndex *
HdEngine::CreateRenderIndex(HdRenderDelegate *renderDelegate)
{
    Hd_RenderIndexManager &riMgr = Hd_RenderIndexManager::GetInstance();

    return riMgr.CreateRenderIndex(renderDelegate);
}

// static
void
HdEngine::AddRenderIndexReference(HdRenderIndex *renderIndex)
{
    if (renderIndex == nullptr) {
        TF_CODING_ERROR("Null context passed to add render index reference");
        return;
    }

    Hd_RenderIndexManager &riMgr = Hd_RenderIndexManager::GetInstance();

    riMgr.AddRenderIndexReference(renderIndex);
}

// static
void
HdEngine::ReleaseRenderIndex(HdRenderIndex *renderIndex)
{
    if (renderIndex == nullptr) {
        // No error reported as releasing null in the event of an error
        // is desirable use-case.
        return;
    }

    Hd_RenderIndexManager &riMgr = Hd_RenderIndexManager::GetInstance();

    riMgr.ReleaseRenderIndex(renderIndex);
}

void 
HdEngine::SetTaskContextData(const TfToken &id, VtValue &data)
{
    // See if the token exists in the context and if not add it.
    std::pair<HdTaskContext::iterator, bool> result =
                                                 _taskContext.emplace(id, data);
    if (!result.second) {
        // Item wasn't new, so need to update it
        result.first->second = data;
    }
}

void
HdEngine::RemoveTaskContextData(const TfToken &id)
{
    _taskContext.erase(id);
}

void
HdEngine::_InitCaps() const
{
    // Make sure we initialize caps in main thread.
    HdRenderContextCaps::GetInstance();
}

void
HdEngine::Execute(HdRenderIndex& index, HdTaskSharedPtrVector const &tasks)
{
    // Note: For Hydra Stream render delegate.
    //
    //
    // The following order is important, be careful.
    //
    // If Sync updates topology varying prims, it triggers both:
    //   1. changing drawing coordinate and bumps up the global collection
    //      version to invalidate the (indirect) batch.
    //   2. marking garbage collection needed so that the unused BAR
    //      resources will be reclaimed.
    //   Also resizing ranges likely cause the buffer reallocation
    //   (==drawing coordinate changes) anyway.
    //
    // Note that the garbage collection also changes the drawing coordinate,
    // so the collection should be invalidated in that case too.
    //
    // Once we reflect all conditions which provoke the batch recompilation
    // into the collection dirtiness, we can call
    // HdRenderPass::GetCommandBuffer() to get the right batch.

    _InitCaps();

    // --------------------------------------------------------------------- //
    // DATA DISCOVERY PHASE
    // --------------------------------------------------------------------- //
    // Discover all required input data needed to render the required render
    // prim representations. At this point, we must read enough data to
    // establish the resource dependency graph, but we do not yet populate CPU-
    // nor GPU-memory with data.

    // As a result of the next call, the resource registry will be populated
    // with both BufferSources that need to be resolved (possibly generating
    // data on the CPU) and computations to run on the GPU.


    // Process all pending dirty lists
    index.SyncAll(tasks, &_taskContext);

    HdRenderDelegate *renderDelegate = index.GetRenderDelegate();
    renderDelegate->CommitResources(&index.GetChangeTracker());

    TF_FOR_ALL(it, tasks) {
        (*it)->Execute(&_taskContext);
    }
}

void
HdEngine::ReloadAllShaders(HdRenderIndex& index)
{
    HdChangeTracker &tracker = index.GetChangeTracker();

    // 1st dirty all rprims, so they will trigger shader reload
    tracker.MarkAllRprimsDirty(HdChangeTracker::AllDirty);

    // Dirty all surface shaders
    SdfPathVector shaders = index.GetSprimSubtree(HdPrimTypeTokens->shader,
                                                  SdfPath::EmptyPath());

    for (SdfPathVector::iterator shaderIt  = shaders.begin();
                                 shaderIt != shaders.end();
                               ++shaderIt) {

        tracker.MarkSprimDirty(*shaderIt, HdChangeTracker::AllDirty);
    }

    // Invalidate Geometry shader cache in Resource Registry.
    HdResourceRegistry::GetInstance().InvalidateGeometricShaderRegistry();

    // Fallback Shader
    HdShader *shader = static_cast<HdShader *>(
                              index.GetFallbackSprim(HdPrimTypeTokens->shader));
    shader->Reload();


    // Note: Several Shaders are not currently captured in this
    // - Lighting Shaders
    // - Render Pass Shaders
    // - Culling Shader

}

class Hd_DrawTask final : public HdTask
{
public:
    Hd_DrawTask(HdRenderPassSharedPtr const &renderPass,
                HdRenderPassStateSharedPtr const &renderPassState)
    : HdTask()
    , _renderPass(renderPass)
    , _renderPassState(renderPassState)
    {
    }

protected:
    virtual void _Sync( HdTaskContext* ctx) override
    {
        _renderPass->Sync();
        _renderPassState->Sync();
    }

    virtual void _Execute(HdTaskContext* ctx) override
    {
        _renderPassState->Bind();
        _renderPass->Execute(_renderPassState);
        _renderPassState->Unbind();
    }

private:
    HdRenderPassSharedPtr _renderPass;
    HdRenderPassStateSharedPtr _renderPassState;
};

void 
HdEngine::Draw(HdRenderIndex& index,
               HdRenderPassSharedPtr const &renderPass,
               HdRenderPassStateSharedPtr const &renderPassState)
{
    // DEPRECATED : use Execute() instead

    // XXX: remaining client codes are
    //
    //   HdxIntersector::Query
    //   Hd_TestDriver::Draw
    //   UsdImaging_TestDriver::Draw
    //   PxUsdGeomGL_TestDriver::Draw
    //   HfkCurvesVMP::draw
    //
    HD_TRACE_FUNCTION();

    // Create Dummy task to use with Execute API.
    HdTaskSharedPtrVector tasks;
    tasks.push_back(boost::make_shared<Hd_DrawTask>(renderPass,
                                                    renderPassState));

    Execute(index, tasks);
}

PXR_NAMESPACE_CLOSE_SCOPE

